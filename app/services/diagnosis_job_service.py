import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.ai import AIProviderError, create_ai_gateway
from app.config import settings
from app.database import SessionLocal
from app.models import (
    AIRequestLog,
    AISafetyAdjustment,
    Diagnosis,
    DiagnosisAnswer,
    DiagnosisJob,
    DiagnosisQuestion,
    DiagnosisRevision,
    Plant,
    PlantPhoto,
    User,
    UserNotification,
)
from app.schemas import DiagnosisCreate
from app.services.ai_safety import safety_identifier_for_user
from app.services.safety_policy_service import enforce_diagnosis_safety

logger = logging.getLogger(__name__)
worker_gateway = create_ai_gateway(settings.ai_provider)


class RetryableDiagnosisJob(RuntimeError):
    pass


def _public_ai_error(http_status: int) -> str:
    return {
        422: "Изображение не подходит для анализа",
        429: "Временно превышен лимит AI-провайдера",
        504: "AI-провайдер не ответил вовремя",
    }.get(http_status, "AI-анализ временно недоступен")


def _claim_job(db, job_id: str) -> tuple[DiagnosisJob | None, str | None]:
    """Atomically acquire a renewable execution lease before any paid AI call."""
    now = datetime.now(UTC)
    token = str(uuid4())
    lease_seconds = max(900, settings.celery_task_timeout_seconds * 2)
    claimed = db.execute(
        update(DiagnosisJob)
        .where(
            DiagnosisJob.id == job_id,
            or_(
                DiagnosisJob.status == "queued",
                and_(
                    DiagnosisJob.status == "running",
                    or_(DiagnosisJob.lease_expires_at.is_(None),
                        DiagnosisJob.lease_expires_at < now),
                ),
            ),
        )
        .values(
            status="running",
            execution_token=token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            started_at=now,
            attempts=DiagnosisJob.attempts + 1,
            error_type=None,
            error_message=None,
        )
        .returning(DiagnosisJob.id)
    ).scalar_one_or_none()
    db.commit()
    if claimed is None:
        return db.get(DiagnosisJob, job_id), None
    return db.get(DiagnosisJob, job_id), token


def _still_owns_lease(db, job_id: str, token: str) -> bool:
    return db.scalar(select(DiagnosisJob.execution_token).where(
        DiagnosisJob.id == job_id,
        DiagnosisJob.status == "running",
    )) == token


def is_fallback_response(model_name: str | None) -> bool:
    """True, когда ответ сформировал резервный текстовый провайдер, а не основной."""
    return bool(
        model_name
        and settings.ai_provider != "deepseek"
        and model_name == settings.deepseek_model
    )


def _ready_notification_texts(language: str, plant_name: str, status: str, degraded: bool) -> tuple[str, str]:
    titles = {"ru": "Диагностика готова", "lv": "Diagnostika gatava", "en": "Diagnosis ready"}
    bodies = {
        "ru": {
            "needs_confirmation": "найдены возможные причины, требуется подтверждение",
            "preliminary": "предварительная оценка готова",
            "expert_required": "рекомендована консультация специалиста",
            "cannot_analyze": "не удалось проанализировать входные данные",
        },
        "lv": {
            "needs_confirmation": "atrastas iespējamās cēloņi, nepieciešams apstiprinājums",
            "preliminary": "sagatavots sākotnējais novērtējums",
            "expert_required": "ieteicama speciālista konsultācija",
            "cannot_analyze": "neizdevās analizēt ievades datus",
        },
        "en": {
            "needs_confirmation": "possible causes found; confirmation needed",
            "preliminary": "preliminary assessment is ready",
            "expert_required": "consulting a specialist is recommended",
            "cannot_analyze": "the input could not be analyzed",
        },
    }
    degraded_notes = {
        "ru": " ⚠️ Основной AI-провайдер временно недоступен: результат сформировал резервный текстовый провайдер без доступа к фотографиям — считайте его предварительным.",
        "lv": " ⚠️ Galvenais AI pakalpojums īslaicīgi nav pieejams: rezultātu sagatavoja rezerves teksta pakalpojums bez piekļuves fotogrāfijām — uzskatiet to par sākotnēju.",
        "en": " ⚠️ The primary AI provider is temporarily unavailable: a backup text-only provider without photo access produced this result — treat it as preliminary.",
    }
    lang = language if language in titles else "ru"
    body = f"{plant_name}: {bodies[lang].get(status, bodies[lang]['preliminary'])}"
    if degraded:
        body += degraded_notes[lang]
    return titles[lang], body


def _notify_diagnosis_ready(db: Session, *, user: User, plant: Plant, diagnosis: Diagnosis, responder_model: str) -> None:
    """Одноразовое уведомление о готовности диагноза (в приложении и Telegram)."""
    key = f"diagnosis_ready:{diagnosis.id}"
    if db.scalar(select(UserNotification.id).where(UserNotification.deduplication_key == key)):
        return
    degraded = is_fallback_response(responder_model)
    title, body = _ready_notification_texts(user.language, plant.name, diagnosis.status, degraded)
    db.add(UserNotification(
        user_id=user.id,
        kind="diagnosis_ready",
        title=title,
        body=body,
        event_at=datetime.now(UTC),
        deduplication_key=key,
    ))


def _notify_diagnosis_failed(db: Session, *, user: User, plant: Plant, job: DiagnosisJob) -> None:
    """Одноразовое уведомление о финальной неудаче диагностики (после исчерпания повторов)."""
    key = f"diagnosis_failed:{job.id}"
    if db.scalar(select(UserNotification.id).where(UserNotification.deduplication_key == key)):
        return
    prefixes = {"ru": "анализ не выполнен", "lv": "analīze neizdevās", "en": "the analysis could not be completed"}
    suffixes = {"ru": "Попробуйте позже.", "lv": "Mēģiniet vēlreiz vēlāk.", "en": "Please try again later."}
    titles = {"ru": "Диагностика не выполнена", "lv": "Diagnostika neizdevās", "en": "Diagnosis failed"}
    lang = user.language if user.language in titles else "ru"
    body = f"{plant.name}: {prefixes[lang]} — {job.error_message or prefixes[lang]}. {suffixes[lang]}"
    db.add(UserNotification(
        user_id=user.id,
        kind="diagnosis_failed",
        title=titles[lang],
        body=body,
        event_at=datetime.now(UTC),
        deduplication_key=key,
    ))


def _add_revision(db, diagnosis: Diagnosis, result, model_name: str, prompt_version: str, version: int) -> None:
    db.add(DiagnosisRevision(
        diagnosis_id=diagnosis.id,
        version=version,
        status=result.analysis_status,
        result=result.model_dump(),
        model_name=model_name,
        prompt_version=prompt_version,
    ))
    for position, text in enumerate(result.questions, start=1):
        db.add(DiagnosisQuestion(
            diagnosis_id=diagnosis.id,
            revision_number=version,
            position=position,
            text=text,
        ))


def _load_job_context(db, job: DiagnosisJob):
    user = db.get(User, job.user_id)
    plant = db.get(Plant, job.plant_id)
    if not user or not plant:
        raise ValueError("Job owner or plant no longer exists")
    if job.operation == "create":
        request = DiagnosisCreate.model_validate(job.payload)
        diagnosis = None
        answers: list[str] = []
    elif job.operation == "reanalyze":
        diagnosis = db.get(Diagnosis, job.diagnosis_id)
        if not diagnosis or diagnosis.plant_id != plant.id:
            raise ValueError("Diagnosis for reanalysis no longer exists")
        request = DiagnosisCreate(
            plant_id=plant.id,
            symptoms=diagnosis.symptoms,
            damaged_part=diagnosis.damaged_part,
            photo_ids=diagnosis.photo_ids,
        )
        answers = list(db.scalars(
            select(DiagnosisAnswer.answer)
            .join(DiagnosisQuestion)
            .where(DiagnosisQuestion.diagnosis_id == diagnosis.id)
            .order_by(DiagnosisQuestion.revision_number, DiagnosisQuestion.position)
        ))
    else:
        raise ValueError(f"Unsupported diagnosis job operation: {job.operation}")
    photo_ids = set(request.photo_ids)
    photos = list(db.scalars(select(PlantPhoto).where(
        PlantPhoto.plant_id == plant.id,
        PlantPhoto.id.in_(photo_ids),
    )))
    if {photo.id for photo in photos} != photo_ids:
        raise ValueError("Job photos no longer exist")
    return user, plant, diagnosis, request, photos, answers


def process_diagnosis_job(
    job_id: str,
    *,
    gateway=None,
    retry_transient: bool = False,
    task_id: str | None = None,
) -> int | None:
    del task_id  # Celery IDs are observability metadata; the DB lease is authoritative.
    gateway = gateway or worker_gateway
    with SessionLocal() as db:
        job, execution_token = _claim_job(db, job_id)
        if not job or execution_token is None:
            return job.diagnosis_id if job and job.status == "succeeded" else None

        try:
            user, plant, diagnosis, request, photos, answers = _load_job_context(db, job)
            analysis = gateway.analyze(
                plant,
                request,
                photos,
                answers,
                safety_identifier=safety_identifier_for_user(user.id),
            )
            if not _still_owns_lease(db, job_id, execution_token):
                logger.warning("diagnosis_job_lease_lost", extra={"job_id": job_id})
                db.rollback()
                return None

            result, safety_decision = enforce_diagnosis_safety(db, analysis.result)
            responder_model = getattr(analysis, "responder_model", None) or gateway.model_name
            if diagnosis is None:
                diagnosis = Diagnosis(
                    plant_id=plant.id,
                    symptoms=request.symptoms,
                    damaged_part=request.damaged_part.value,
                    status=result.analysis_status,
                    result=result.model_dump(),
                    model_name=responder_model,
                    prompt_version=gateway.prompt_version,
                    current_revision=1,
                )
                diagnosis.photos = photos
                db.add(diagnosis)
                db.flush()
                _add_revision(db, diagnosis, result, responder_model, gateway.prompt_version, 1)
            else:
                version = diagnosis.current_revision + 1
                diagnosis.status = result.analysis_status
                diagnosis.result = result.model_dump()
                diagnosis.model_name = responder_model
                diagnosis.prompt_version = gateway.prompt_version
                diagnosis.current_revision = version
                _add_revision(db, diagnosis, result, responder_model, gateway.prompt_version, version)

            if safety_decision.adjusted:
                db.add(AISafetyAdjustment(
                    user_id=user.id,
                    diagnosis_id=diagnosis.id,
                    job_id=job.id,
                    reason_codes=list(safety_decision.reason_codes),
                    removed_action_count=safety_decision.removed_action_count,
                ))

            usage = analysis.usage
            db.add(AIRequestLog(
                user_id=user.id,
                diagnosis_id=diagnosis.id,
                request_id=usage.request_id,
                provider=settings.ai_provider,
                model_name=responder_model,
                prompt_version=gateway.prompt_version,
                success=True,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                response_ms=usage.response_ms,
                estimated_cost=usage.estimated_cost,
            ))
            job.diagnosis_id = diagnosis.id
            job.status = "succeeded"
            job.execution_token = None
            job.lease_expires_at = None
            job.completed_at = datetime.now(UTC)
            _notify_diagnosis_ready(db, user=user, plant=plant, diagnosis=diagnosis, responder_model=responder_model)
            db.commit()
            return diagnosis.id
        except AIProviderError as exc:
            db.rollback()
            job = db.scalar(select(DiagnosisJob).where(
                DiagnosisJob.id == job_id,
                DiagnosisJob.execution_token == execution_token,
            ))
            if not job:
                return None
            retryable = retry_transient and exc.http_status in {429, 503, 504} and job.attempts < 3
            job.status = "queued" if retryable else "failed"
            job.execution_token = None
            job.lease_expires_at = None
            job.error_type = exc.error_type
            job.error_message = _public_ai_error(exc.http_status)
            job.completed_at = None if retryable else datetime.now(UTC)
            db.add(AIRequestLog(
                user_id=job.user_id,
                diagnosis_id=job.diagnosis_id,
                request_id=exc.request_id,
                provider=settings.ai_provider,
                model_name=getattr(exc, "model_name", None) or gateway.model_name,
                prompt_version=gateway.prompt_version,
                success=False,
                error_type=exc.error_type,
                http_status=exc.http_status,
                response_ms=exc.response_ms,
            ))
            db.commit()
            if retryable:
                raise RetryableDiagnosisJob(job.error_message) from exc
            _notify_diagnosis_failed(db, user=user, plant=plant, job=job)
            db.commit()
            return None
        except Exception:
            db.rollback()
            logger.exception("Background diagnosis job failed", extra={"job_id": job_id})
            db.execute(
                update(DiagnosisJob)
                .where(DiagnosisJob.id == job_id, DiagnosisJob.execution_token == execution_token)
                .values(
                    status="failed",
                    execution_token=None,
                    lease_expires_at=None,
                    error_type="internal_error",
                    error_message="Не удалось завершить фоновый анализ",
                    completed_at=datetime.now(UTC),
                )
            )
            db.commit()
            return None
