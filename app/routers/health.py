import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import gateway
from app.config import settings
from app.database import engine, get_db
from app.models import (
    AIRequestLog,
    DiagnosisJob,
    PartnerInvoice,
    ProductLead,
    UserNotification,
)
from app.paths import REPO_ROOT
from app.services.alert_service import dispatch_operational_alert
from app.services.metrics_service import render_metrics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "ai_provider": settings.ai_provider,
        "demo_mode": gateway.ai_gateway.demo_mode,
        "demo_login_enabled": (
            settings.demo_login_enabled and settings.environment.lower() != "production"
        ),
        "diagnosis_execution_mode": settings.diagnosis_execution_mode,
        "partner_commerce_enabled": settings.partner_commerce_enabled,
        "b2b_invoicing_enabled": settings.b2b_invoicing_enabled,
    }


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
def readiness() -> dict:
    checks: dict[str, str] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            from alembic.config import Config
            from alembic.migration import MigrationContext
            from alembic.script import ScriptDirectory

            current = MigrationContext.configure(connection).get_current_revision()
            # Под WSGI текущая директория может отличаться от корня репозитория,
            # поэтому пути Alembic якорятся к файлу, а не к os.getcwd().
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
            head = ScriptDirectory.from_config(config).get_current_head()
            if current != head:
                raise RuntimeError(f"database migration is {current!r}, expected {head!r}")
        checks["database"] = "ok"
        checks["migrations"] = "ok"
    except Exception as exc:
        logger.exception("readiness database check failed")
        dispatch_operational_alert(
            "readiness_failed", details={"check": "database", "error_type": type(exc).__name__}
        )
        raise HTTPException(
            503, {"status": "not_ready", "check": "database", "error": str(exc)[:200]}
        ) from exc
    if settings.diagnosis_execution_mode == "celery":
        try:
            from redis import Redis

            from app.tasks import celery_app

            Redis.from_url(
                settings.celery_broker_url, socket_connect_timeout=1, socket_timeout=1
            ).ping()
            checks["redis"] = "ok"
            workers = celery_app.control.inspect(timeout=1).ping() or {}
            if not workers:
                raise RuntimeError("no Celery workers responded")
            checks["worker"] = "ok"
        except Exception as exc:
            logger.exception("readiness queue check failed")
            dispatch_operational_alert(
                "readiness_failed", details={"check": "queue", "error_type": type(exc).__name__}
            )
            raise HTTPException(
                503, {"status": "not_ready", "check": "queue", "error": str(exc)[:200]}
            ) from exc
    return {"status": "ready", "checks": checks}


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, db: Session = Depends(get_db)) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(404, "Metrics disabled")
    if settings.metrics_token:
        authorization = request.headers.get("authorization", "")
        expected = f"Bearer {settings.metrics_token}"
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Metrics authentication required")
    gauges = {
        "diagnosis_jobs_queued": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "queued")
        )
        or 0,
        "diagnosis_jobs_running": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "running")
        )
        or 0,
        "diagnosis_jobs_failed": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "failed")
        )
        or 0,
        "ai_requests_failed": db.scalar(
            select(func.count(AIRequestLog.id)).where(AIRequestLog.success.is_(False))
        )
        or 0,
        "notifications_pending": db.scalar(
            select(func.count(UserNotification.id)).where(UserNotification.read_at.is_(None))
        )
        or 0,
        "partner_conversions_pending": db.scalar(
            select(func.count(ProductLead.id)).where(ProductLead.status == "conversion_claimed")
        )
        or 0,
        "partner_invoices_outstanding_cents": db.scalar(
            select(func.coalesce(func.sum(PartnerInvoice.total_cents), 0)).where(
                PartnerInvoice.status == "issued"
            )
        )
        or 0,
    }
    return Response(render_metrics(gauges), media_type="text/plain; version=0.0.4; charset=utf-8")
