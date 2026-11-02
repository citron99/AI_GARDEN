"""Выгрузка всех данных аккаунта пользователя (GDPR export)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AIRequestLog,
    AuthSession,
    CareEvent,
    Diagnosis,
    DiagnosisAnswer,
    DiagnosisFeedback,
    DiagnosisJob,
    DiagnosisQuestion,
    DiagnosisRevision,
    Garden,
    Plant,
    PlantPhoto,
    ProductLead,
    Reminder,
    Subscription,
    TelegramAccount,
    TelegramUpdate,
    User,
    UserNotification,
)


def _row(item, exclude: set[str] | None = None) -> dict:
    skip = exclude or set()
    return {
        column.name: getattr(item, column.name)
        for column in item.__table__.columns
        if column.name not in skip
    }


def build_account_export(user: User, db: Session) -> dict:
    gardens = list(db.scalars(select(Garden).where(Garden.user_id == user.id).order_by(Garden.id)))
    garden_ids = [item.id for item in gardens]
    plants = (
        list(db.scalars(select(Plant).where(Plant.garden_id.in_(garden_ids)).order_by(Plant.id)))
        if garden_ids
        else []
    )
    plant_ids = [item.id for item in plants]
    diagnoses = (
        list(
            db.scalars(
                select(Diagnosis).where(Diagnosis.plant_id.in_(plant_ids)).order_by(Diagnosis.id)
            )
        )
        if plant_ids
        else []
    )
    care_events = (
        list(
            db.scalars(
                select(CareEvent).where(CareEvent.plant_id.in_(plant_ids)).order_by(CareEvent.id)
            )
        )
        if plant_ids
        else []
    )
    reminders = (
        list(
            db.scalars(
                select(Reminder).where(Reminder.plant_id.in_(plant_ids)).order_by(Reminder.id)
            )
        )
        if plant_ids
        else []
    )
    photos = (
        list(
            db.scalars(
                select(PlantPhoto).where(PlantPhoto.plant_id.in_(plant_ids)).order_by(PlantPhoto.id)
            )
        )
        if plant_ids
        else []
    )
    diagnosis_ids = [item.id for item in diagnoses]
    revisions = (
        list(
            db.scalars(
                select(DiagnosisRevision)
                .where(DiagnosisRevision.diagnosis_id.in_(diagnosis_ids))
                .order_by(DiagnosisRevision.id)
            )
        )
        if diagnosis_ids
        else []
    )
    questions = (
        list(
            db.scalars(
                select(DiagnosisQuestion)
                .where(DiagnosisQuestion.diagnosis_id.in_(diagnosis_ids))
                .order_by(DiagnosisQuestion.id)
            )
        )
        if diagnosis_ids
        else []
    )
    question_ids = [item.id for item in questions]
    answers = (
        list(
            db.scalars(
                select(DiagnosisAnswer)
                .where(DiagnosisAnswer.question_id.in_(question_ids))
                .order_by(DiagnosisAnswer.id)
            )
        )
        if question_ids
        else []
    )
    feedback = list(
        db.scalars(
            select(DiagnosisFeedback)
            .where(DiagnosisFeedback.user_id == user.id)
            .order_by(DiagnosisFeedback.id)
        )
    )
    notifications = list(
        db.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == user.id)
            .order_by(UserNotification.id)
        )
    )
    ai_requests = list(
        db.scalars(
            select(AIRequestLog).where(AIRequestLog.user_id == user.id).order_by(AIRequestLog.id)
        )
    )
    diagnosis_jobs = list(
        db.scalars(
            select(DiagnosisJob)
            .where(DiagnosisJob.user_id == user.id)
            .order_by(DiagnosisJob.created_at)
        )
    )
    auth_sessions = list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.created_at)
        )
    )
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    telegram_account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user.id))
    telegram_updates = (
        list(
            db.scalars(
                select(TelegramUpdate)
                .where(TelegramUpdate.chat_id == telegram_account.chat_id)
                .order_by(TelegramUpdate.id)
            )
        )
        if telegram_account
        else []
    )
    commercial_interactions = list(
        db.scalars(
            select(ProductLead).where(ProductLead.user_id == user.id).order_by(ProductLead.id)
        )
    )
    return {
        "exported_at": datetime.now(UTC),
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "language": user.language,
            "region": user.region,
            "created_at": user.created_at,
        },
        "gardens": [_row(item, {"user_id"}) for item in gardens],
        "plants": [_row(item) for item in plants],
        "diagnoses": [
            {
                "id": item.id,
                "plant_id": item.plant_id,
                "symptoms": item.symptoms,
                "damaged_part": item.damaged_part,
                "result": item.result,
                "model_name": item.model_name,
                "prompt_version": item.prompt_version,
                "created_at": item.created_at,
            }
            for item in diagnoses
        ],
        "diagnosis_revisions": [_row(item) for item in revisions],
        "diagnosis_questions": [_row(item) for item in questions],
        "diagnosis_answers": [_row(item) for item in answers],
        "diagnosis_feedback": [_row(item, {"user_id"}) for item in feedback],
        "care_events": [_row(item) for item in care_events],
        "reminders": [_row(item) for item in reminders],
        "photos": [
            {
                "id": item.id,
                "plant_id": item.plant_id,
                "content_type": item.content_type,
                "created_at": item.created_at,
                "download_url": f"/api/v1/photos/{item.id}",
            }
            for item in photos
        ],
        "commercial_interactions": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "diagnosis_id": item.diagnosis_id,
                "source": item.source,
                "status": item.status,
                "partner_reference": item.partner_reference,
                "conversion_value_cents": item.conversion_value_cents,
                "claimed_at": item.claimed_at,
                "confirmed_at": item.confirmed_at,
                "created_at": item.created_at,
            }
            for item in commercial_interactions
        ],
        "notifications": [_row(item, {"user_id"}) for item in notifications],
        "ai_request_logs": [_row(item, {"user_id"}) for item in ai_requests],
        "diagnosis_jobs": [
            _row(item, {"user_id", "execution_token", "celery_task_id"}) for item in diagnosis_jobs
        ],
        "auth_sessions": [
            {
                "id": item.id,
                "expires_at": item.expires_at,
                "revoked_at": item.revoked_at,
                "created_at": item.created_at,
                "last_used_at": item.last_used_at,
            }
            for item in auth_sessions
        ],
        "subscription": _row(subscription, {"user_id"}) if subscription else None,
        "telegram_account": _row(telegram_account, {"user_id"}) if telegram_account else None,
        "telegram_updates": [_row(item) for item in telegram_updates],
    }
