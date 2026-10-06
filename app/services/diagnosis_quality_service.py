"""Aggregate privacy-safe diagnosis quality evidence for the admin cockpit."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIRequestLog, AISafetyAdjustment, Diagnosis, DiagnosisFeedback
from app.services.diagnosis_job_service import is_fallback_response


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * fraction) - 1)]


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _result_value(diagnosis: Diagnosis, key: str) -> str | None:
    result = diagnosis.result if isinstance(diagnosis.result, dict) else {}
    value = result.get(key)
    return str(value) if value is not None else None


def _window(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    provider: str | None,
    prompt_version: str | None,
) -> dict[str, Any]:
    request_query = select(AIRequestLog).where(
        AIRequestLog.created_at >= start,
        AIRequestLog.created_at < end,
    )
    if provider:
        request_query = request_query.where(AIRequestLog.provider == provider)
    if prompt_version:
        request_query = request_query.where(AIRequestLog.prompt_version == prompt_version)
    requests = list(db.scalars(request_query.order_by(AIRequestLog.created_at)))

    diagnosis_query = select(Diagnosis).where(
        Diagnosis.created_at >= start,
        Diagnosis.created_at < end,
    )
    if provider or prompt_version:
        scoped_ids = {item.diagnosis_id for item in requests if item.diagnosis_id is not None}
        diagnoses = (
            list(db.scalars(diagnosis_query.where(Diagnosis.id.in_(scoped_ids))))
            if scoped_ids
            else []
        )
    else:
        diagnoses = list(db.scalars(diagnosis_query))
    diagnosis_ids = {item.id for item in diagnoses}
    feedback = (
        list(
            db.scalars(
                select(DiagnosisFeedback).where(DiagnosisFeedback.diagnosis_id.in_(diagnosis_ids))
            )
        )
        if diagnosis_ids
        else []
    )
    adjustments = (
        list(
            db.scalars(
                select(AISafetyAdjustment).where(
                    AISafetyAdjustment.diagnosis_id.in_(diagnosis_ids),
                    AISafetyAdjustment.created_at >= start,
                    AISafetyAdjustment.created_at < end,
                )
            )
        )
        if diagnosis_ids
        else []
    )

    cannot_analyze = sum(
        _result_value(item, "analysis_outcome") == "cannot_analyze"
        or _result_value(item, "analysis_status") == "cannot_analyze"
        for item in diagnoses
    )
    expert_required = sum(
        _result_value(item, "analysis_status") == "expert_required" for item in diagnoses
    )
    helpful = sum(item.helpful for item in feedback)
    successful = sum(item.success for item in requests)
    adjusted_ids = {item.diagnosis_id for item in adjustments if item.diagnosis_id is not None}
    ratings = [item.rating for item in feedback]
    latencies = [item.response_ms for item in requests]

    kpis: dict[str, int | float | None] = {
        "diagnoses": len(diagnoses),
        "ai_requests": len(requests),
        "ai_success_rate": _rate(successful, len(requests)),
        "feedback_coverage_rate": _rate(
            len({item.diagnosis_id for item in feedback}), len(diagnoses)
        ),
        "average_rating": round(mean(ratings), 2) if ratings else None,
        "user_helpful_rate": _rate(helpful, len(feedback)) if feedback else None,
        "cannot_analyze_rate": _rate(cannot_analyze, len(diagnoses)),
        "expert_required_rate": _rate(expert_required, len(diagnoses)),
        "safety_adjustment_rate": _rate(len(adjusted_ids), len(diagnoses)),
        "removed_unsafe_actions": sum(item.removed_action_count for item in adjustments),
        "reanalysis_rate": _rate(
            sum(item.current_revision > 1 for item in diagnoses), len(diagnoses)
        ),
        "fallback_rate": _rate(
            sum(is_fallback_response(item.model_name) for item in diagnoses), len(diagnoses)
        ),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "estimated_cost": round(sum(item.estimated_cost for item in requests), 6),
    }
    return {
        "kpis": kpis,
        "diagnoses": diagnoses,
        "requests": requests,
        "feedback": feedback,
        "adjustments": adjustments,
    }


def _quality_score(kpis: dict[str, int | float | None]) -> int:
    if not kpis["diagnoses"] or not kpis["ai_requests"]:
        return 0
    score = 100.0
    score -= (100 - float(kpis["ai_success_rate"] or 0)) * 0.30
    score -= float(kpis["cannot_analyze_rate"] or 0) * 0.20
    if kpis["user_helpful_rate"] is not None:
        score -= (100 - float(kpis["user_helpful_rate"] or 0)) * 0.15
    if kpis["average_rating"] is not None:
        score -= max(0, 4 - float(kpis["average_rating"] or 0)) * 8
    score -= min(float(kpis["safety_adjustment_rate"] or 0), 30) * 0.15
    return max(0, min(100, round(score)))


def _signals(kpis: dict[str, int | float | None]) -> list[dict[str, Any]]:
    rules = [
        ("ai_success_rate", "AI request success", 97, "minimum", "critical"),
        ("average_rating", "Average user rating", 4.0, "minimum", "watch"),
        ("user_helpful_rate", "User-reported helpfulness", 75, "minimum", "watch"),
        ("cannot_analyze_rate", "Cannot-analyze rate", 15, "maximum", "watch"),
        ("latency_p95_ms", "P95 latency", 15_000, "maximum", "watch"),
    ]
    signals = []
    for metric, label, threshold, direction, severity in rules:
        actual = kpis.get(metric)
        breached = actual is not None and (
            (direction == "minimum" and float(actual) < threshold)
            or (direction == "maximum" and float(actual) > threshold)
        )
        signals.append(
            {
                "metric": metric,
                "label": label,
                "actual": actual,
                "threshold": threshold,
                "direction": direction,
                "status": (
                    "insufficient_data" if actual is None else severity if breached else "healthy"
                ),
            }
        )
    return signals


def _slices(window: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[AIRequestLog]] = defaultdict(list)
    for item in window["requests"]:
        groups[(item.provider, item.model_name, item.prompt_version)].append(item)
    feedback_by_diagnosis: dict[int, list[DiagnosisFeedback]] = defaultdict(list)
    for item in window["feedback"]:
        feedback_by_diagnosis[item.diagnosis_id].append(item)
    rows = []
    for (provider, model, prompt), requests in groups.items():
        diagnosis_ids = {item.diagnosis_id for item in requests if item.diagnosis_id is not None}
        feedback = [
            row for diagnosis_id in diagnosis_ids for row in feedback_by_diagnosis[diagnosis_id]
        ]
        ratings = [item.rating for item in feedback]
        rows.append(
            {
                "provider": provider,
                "model_name": model,
                "prompt_version": prompt,
                "requests": len(requests),
                "diagnoses": len(diagnosis_ids),
                "success_rate": _rate(sum(item.success for item in requests), len(requests)),
                "latency_p95_ms": percentile([item.response_ms for item in requests], 0.95),
                "average_rating": round(mean(ratings), 2) if ratings else None,
                "user_helpful_rate": (
                    _rate(sum(item.helpful for item in feedback), len(feedback))
                    if feedback
                    else None
                ),
                "estimated_cost": round(sum(item.estimated_cost for item in requests), 6),
            }
        )
    return sorted(rows, key=lambda item: (-item["requests"], item["provider"], item["model_name"]))


def _trend(window: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"diagnoses": 0, "requests": 0, "success": 0, "latencies": [], "ratings": []}
    )
    for item in window["diagnoses"]:
        buckets[item.created_at.date().isoformat()]["diagnoses"] += 1
    for item in window["requests"]:
        bucket = buckets[item.created_at.date().isoformat()]
        bucket["requests"] += 1
        bucket["success"] += int(item.success)
        bucket["latencies"].append(item.response_ms)
    diagnosis_dates = {item.id: item.created_at.date().isoformat() for item in window["diagnoses"]}
    for item in window["feedback"]:
        if item.diagnosis_id in diagnosis_dates:
            buckets[diagnosis_dates[item.diagnosis_id]]["ratings"].append(item.rating)
    return [
        {
            "date": day,
            "diagnoses": data["diagnoses"],
            "success_rate": _rate(data["success"], data["requests"]),
            "latency_p95_ms": percentile(data["latencies"], 0.95),
            "average_rating": round(mean(data["ratings"]), 2) if data["ratings"] else None,
        }
        for day, data in sorted(buckets.items())
    ]


def build_diagnosis_quality_report(
    db: Session,
    *,
    days: int = 30,
    provider: str | None = None,
    prompt_version: str | None = None,
) -> dict[str, Any]:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    current = _window(db, start, end, provider=provider, prompt_version=prompt_version)
    previous = _window(db, previous_start, start, provider=provider, prompt_version=prompt_version)
    reason_counts: Counter[str] = Counter()
    for adjustment in current["adjustments"]:
        reason_counts.update(str(value) for value in adjustment.reason_codes)
    regressions = []
    for metric, higher_is_better, tolerance in (
        ("ai_success_rate", True, 1.0),
        ("average_rating", True, 0.2),
        ("user_helpful_rate", True, 3.0),
        ("cannot_analyze_rate", False, 2.0),
    ):
        current_value = current["kpis"].get(metric)
        previous_value = previous["kpis"].get(metric)
        if current_value is None or previous_value is None:
            state = "insufficient_data"
            delta = None
        else:
            delta = round(float(current_value) - float(previous_value), 2)
            signed = delta if higher_is_better else -delta
            state = (
                "improved"
                if signed > tolerance
                else "degraded"
                if signed < -tolerance
                else "stable"
            )
        regressions.append(
            {
                "metric": metric,
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "state": state,
            }
        )
    feedback_coverage = float(current["kpis"]["feedback_coverage_rate"] or 0)
    return {
        "period": {
            "days": days,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "provider": provider,
            "prompt_version": prompt_version,
        },
        "quality_score": _quality_score(current["kpis"]),
        "kpis": current["kpis"],
        "signals": _signals(current["kpis"]),
        "slices": _slices(current),
        "trend": _trend(current),
        "regressions": regressions,
        "safety_reasons": [
            {"reason_code": reason, "count": count} for reason, count in reason_counts.most_common()
        ],
        "evidence": {
            "generated_at": end.isoformat(),
            "diagnosis_records": len(current["diagnoses"]),
            "ai_request_logs": len(current["requests"]),
            "feedback_records": len(current["feedback"]),
            "safety_adjustments": len(current["adjustments"]),
            "contains_personal_data": False,
            "limitations": [
                "Helpfulness and ratings are user feedback, not diagnostic ground truth.",
                "Quality score is an operational heuristic, not an agronomic accuracy claim.",
                "Low feedback coverage can bias rating-based metrics.",
            ],
            "complete": bool(current["kpis"]["diagnoses"])
            and bool(current["kpis"]["ai_requests"])
            and feedback_coverage >= 20,
        },
    }
