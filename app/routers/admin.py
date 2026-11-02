from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_admin_user
from app.database import get_db
from app.models import (
    AdminAuditLog,
    AIRequestLog,
    AuthSession,
    Diagnosis,
    DiagnosisFeedback,
    DiagnosisJob,
    Garden,
    Partner,
    PartnerInvoice,
    Plant,
    Product,
    ProductLead,
    User,
)
from app.schemas import (
    AdminAIRequestRead,
    AdminAuditLogRead,
    AdminJobSummaryRead,
    AdminOverviewRead,
    AdminUserSummaryRead,
    DiagnosisQualityReportRead,
)
from app.services.diagnosis_quality_service import build_diagnosis_quality_report

router = APIRouter(prefix="/api/v1/admin")


@router.patch("/users/{user_id}/blocked")
def set_user_blocked(
    user_id: int,
    blocked: bool,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if target.id == admin.id and blocked:
        raise HTTPException(409, "Администратор не может заблокировать собственную учётную запись")
    target.is_blocked = blocked
    if blocked:
        db.query(AuthSession).filter(
            AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None)
        ).update({AuthSession.revoked_at: datetime.now(UTC)}, synchronize_session=False)
    db.add(
        AdminAuditLog(
            admin_user_id=admin.id,
            action="user.block" if blocked else "user.unblock",
            target_type="user",
            target_id=str(target.id),
            details={"email": target.email},
        )
    )
    db.commit()
    return {"user_id": target.id, "blocked": target.is_blocked}


@router.get("/overview", response_model=AdminOverviewRead)
def admin_overview(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    average_rating = db.scalar(select(func.avg(DiagnosisFeedback.rating)))
    return {
        "users": db.scalar(select(func.count(User.id))) or 0,
        "gardens": db.scalar(select(func.count(Garden.id))) or 0,
        "plants": db.scalar(select(func.count(Plant.id))) or 0,
        "diagnoses": db.scalar(select(func.count(Diagnosis.id))) or 0,
        "jobs_queued": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "queued")
        )
        or 0,
        "jobs_running": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "running")
        )
        or 0,
        "jobs_failed": db.scalar(
            select(func.count(DiagnosisJob.id)).where(DiagnosisJob.status == "failed")
        )
        or 0,
        "ai_requests_successful": db.scalar(
            select(func.count(AIRequestLog.id)).where(AIRequestLog.success.is_(True))
        )
        or 0,
        "ai_requests_failed": db.scalar(
            select(func.count(AIRequestLog.id)).where(AIRequestLog.success.is_(False))
        )
        or 0,
        "input_tokens": db.scalar(select(func.coalesce(func.sum(AIRequestLog.input_tokens), 0)))
        or 0,
        "output_tokens": db.scalar(select(func.coalesce(func.sum(AIRequestLog.output_tokens), 0)))
        or 0,
        "estimated_cost": float(
            db.scalar(select(func.coalesce(func.sum(AIRequestLog.estimated_cost), 0))) or 0
        ),
        "average_feedback_rating": float(average_rating) if average_rating is not None else None,
        "partners": db.scalar(select(func.count(Partner.id)).where(Partner.active.is_(True))) or 0,
        "active_products": db.scalar(select(func.count(Product.id)).where(Product.active.is_(True)))
        or 0,
        "product_leads": db.scalar(select(func.count(ProductLead.id))) or 0,
        "pending_partner_conversions": db.scalar(
            select(func.count(ProductLead.id)).where(ProductLead.status == "conversion_claimed")
        )
        or 0,
        "confirmed_partner_conversions": db.scalar(
            select(func.count(ProductLead.id)).where(ProductLead.status == "confirmed")
        )
        or 0,
        "partner_invoices_issued_cents": db.scalar(
            select(func.coalesce(func.sum(PartnerInvoice.total_cents), 0)).where(
                PartnerInvoice.status.in_(("issued", "paid"))
            )
        )
        or 0,
    }


@router.get("/users", response_model=list[AdminUserSummaryRead])
def admin_users(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    garden_count = (
        select(func.count(Garden.id))
        .where(Garden.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    plant_count = (
        select(func.count(Plant.id))
        .join(Garden)
        .where(Garden.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    diagnosis_count = (
        select(func.count(Diagnosis.id))
        .join(Plant)
        .join(Garden)
        .where(Garden.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    rows = db.execute(
        select(User, garden_count, plant_count, diagnosis_count)
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "region": user.region,
            "is_admin": user.is_admin,
            "created_at": user.created_at,
            "gardens_count": gardens,
            "plants_count": plants,
            "diagnoses_count": diagnoses,
        }
        for user, gardens, plants, diagnoses in rows
    ]


@router.get("/diagnosis-jobs", response_model=list[AdminJobSummaryRead])
def admin_diagnosis_jobs(
    status_filter: str | None = Query(default=None, pattern="^(queued|running|succeeded|failed)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(DiagnosisJob)
    if status_filter:
        query = query.where(DiagnosisJob.status == status_filter)
    jobs = list(
        db.scalars(query.order_by(DiagnosisJob.created_at.desc()).offset(offset).limit(limit))
    )
    return [
        {
            "id": job.id,
            "user_id": job.user_id,
            "plant_id": job.plant_id,
            "diagnosis_id": job.diagnosis_id,
            "status": job.status,
            "attempts": job.attempts,
            "error_type": job.error_type,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }
        for job in jobs
    ]


@router.get("/ai-requests", response_model=list[AdminAIRequestRead])
def admin_ai_requests(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    logs = list(
        db.scalars(
            select(AIRequestLog)
            .order_by(AIRequestLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return [
        {
            "request_id": item.request_id,
            "diagnosis_id": item.diagnosis_id,
            "provider": item.provider,
            "model_name": item.model_name,
            "prompt_version": item.prompt_version,
            "success": item.success,
            "error_type": item.error_type,
            "http_status": item.http_status,
            "input_tokens": item.input_tokens,
            "output_tokens": item.output_tokens,
            "response_ms": item.response_ms,
            "estimated_cost": item.estimated_cost,
            "created_at": item.created_at,
        }
        for item in logs
    ]


@router.get("/diagnosis-quality", response_model=DiagnosisQualityReportRead)
def admin_diagnosis_quality(
    days: int = Query(default=30, ge=7, le=365),
    provider: str | None = Query(default=None, min_length=1, max_length=40),
    prompt_version: str | None = Query(default=None, min_length=1, max_length=80),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    return build_diagnosis_quality_report(
        db,
        days=days,
        provider=provider,
        prompt_version=prompt_version,
    )


@router.get("/audit-logs", response_model=list[AdminAuditLogRead])
def admin_audit_logs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[AdminAuditLog]:
    return list(
        db.scalars(
            select(AdminAuditLog)
            .order_by(AdminAuditLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
