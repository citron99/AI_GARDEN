"""Квоты диагностик и постановка заданий в очередь."""

import logging
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import gateway, task_queue
from app.config import settings
from app.models import Diagnosis, DiagnosisJob, Garden, Plant, PlantPhoto, Subscription, User
from app.schemas import DiagnosisCreate

logger = logging.getLogger(__name__)


def enforce_monthly_diagnosis_quota(user: User, db: Session) -> None:
    # PostgreSQL сериализует конкурентные запросы одного пользователя; SQLite
    # игнорирует FOR UPDATE, что приемлемо только для локальной разработки.
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    used = (
        db.scalar(
            select(func.count(DiagnosisJob.id)).where(
                DiagnosisJob.user_id == user.id,
                DiagnosisJob.created_at >= month_start,
                DiagnosisJob.operation == "create",
            )
        )
        or 0
    )
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status.in_(("active", "trialing")),
            or_(Subscription.current_period_end.is_(None), Subscription.current_period_end > now),
        )
    )
    limit = settings.pro_diagnoses_per_month if subscription else settings.free_diagnoses_per_month
    if used >= limit:
        raise HTTPException(429, "Месячный лимит диагностик исчерпан")


def enforce_reanalysis_quota(diagnosis: Diagnosis, db: Session) -> None:
    db.execute(
        select(Diagnosis.id).where(Diagnosis.id == diagnosis.id).with_for_update()
    ).scalar_one()
    db.refresh(diagnosis)
    attempts = (
        db.scalar(
            select(func.count(DiagnosisJob.id)).where(
                DiagnosisJob.diagnosis_id == diagnosis.id,
                DiagnosisJob.operation == "reanalyze",
            )
        )
        or 0
    )
    if attempts >= settings.max_reanalyses_per_diagnosis:
        raise HTTPException(429, "Лимит повторных анализов для этой диагностики исчерпан")
    active_job = db.scalar(
        select(DiagnosisJob.id)
        .where(
            DiagnosisJob.diagnosis_id == diagnosis.id,
            DiagnosisJob.operation == "reanalyze",
            DiagnosisJob.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    if active_job:
        raise HTTPException(409, "Повторный анализ этой диагностики уже выполняется")


def _dispatch_or_fail(job: DiagnosisJob, db: Session) -> DiagnosisJob:
    try:
        task_queue.dispatch_diagnosis_job(job.id, gateway=gateway.ai_gateway)
    except Exception:
        logger.exception("Could not dispatch diagnosis job", extra={"job_id": job.id})
        db.expire_all()
        persisted = db.get(DiagnosisJob, job.id)
        persisted.status = "failed"
        persisted.error_type = "queue_unavailable"
        persisted.error_message = "Очередь диагностики временно недоступна"
        persisted.completed_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(503, "Очередь диагностики временно недоступна")
    db.expire_all()
    return cast(DiagnosisJob, db.get(DiagnosisJob, job.id))


def create_and_dispatch_diagnosis_job(
    payload: DiagnosisCreate, user: User, db: Session
) -> DiagnosisJob:
    plant = db.scalar(
        select(Plant).join(Garden).where(Plant.id == payload.plant_id, Garden.user_id == user.id)
    )
    if not plant:
        raise HTTPException(404, "Растение не найдено")
    photo_ids = set(payload.photo_ids)
    photos = list(
        db.scalars(
            select(PlantPhoto).where(PlantPhoto.plant_id == plant.id, PlantPhoto.id.in_(photo_ids))
        )
    )
    if {photo.id for photo in photos} != photo_ids:
        raise HTTPException(400, "Некоторые фотографии не принадлежат этому растению")
    enforce_monthly_diagnosis_quota(user, db)
    job = DiagnosisJob(
        id=str(uuid4()),
        user_id=user.id,
        plant_id=plant.id,
        status="queued",
        operation="create",
        payload=payload.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    return _dispatch_or_fail(job, db)


def queue_reanalysis_job(
    diagnosis: Diagnosis,
    user: User,
    db: Session,
    *,
    reason: str,
) -> DiagnosisJob:
    enforce_reanalysis_quota(diagnosis, db)
    job = DiagnosisJob(
        id=str(uuid4()),
        user_id=user.id,
        plant_id=diagnosis.plant_id,
        diagnosis_id=diagnosis.id,
        operation="reanalyze",
        status="queued",
        payload={"reason": reason},
    )
    db.add(job)
    db.commit()
    return _dispatch_or_fail(job, db)
