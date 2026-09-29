import json
from pathlib import Path


def build_report(results_path: Path) -> dict[str, float | int]:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    results = payload.get("results", []) if isinstance(payload, dict) else payload
    total = len(results)
    if not total:
        return {"cases": 0}
    not_plants = [item for item in results if item.get("category") == "not_a_plant"]
    healthy = [item for item in results if item.get("category") == "healthy"]
    problem_cases = [item for item in results if item.get("category") in {"disease", "pest", "abiotic"}]
    return {
        "cases": total,
        "successful_case_rate": sum(bool(item.get("successful")) for item in results) / total,
        "top3_cause_recall": (sum(bool(item.get("cause_in_top3")) for item in problem_cases) / len(problem_cases)
                              if problem_cases else 0),
        "input_status_accuracy": sum(bool(item.get("input_status_correct")) for item in results) / total,
        "analysis_outcome_accuracy": sum(bool(item.get("analysis_outcome_correct")) for item in results) / total,
        "not_a_plant_accuracy": (sum(bool(item.get("input_status_correct")) for item in not_plants) / len(not_plants)
                                 if not_plants else 0),
        "healthy_outcome_accuracy": (sum(bool(item.get("analysis_outcome_correct")) for item in healthy) / len(healthy)
                                     if healthy else 0),
        "unsafe_action_rate": sum(bool(item.get("unsafe_action")) for item in results) / total,
        "unsafe_action_free_rate": 1 - sum(bool(item.get("unsafe_action")) for item in results) / total,
        "source_accuracy": (sum(bool(item.get("sources_correct")) for item in problem_cases) / len(problem_cases)
                            if problem_cases else 0),
        "expert_review_queue": sum(bool(item.get("expert_review_required")) for item in results),
        "average_latency_ms": sum(int(item.get("response_ms", 0)) for item in results) / total,
        "estimated_cost": sum(float(item.get("estimated_cost", 0)) for item in results),
    }
