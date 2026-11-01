from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models import Garden, Plant, PlantPhoto, Subscription, User
from app.schemas import (
    AccountDeleteRequest,
    UserProfileUpdate,
    UserRead,
)
from app.services.account_deletion_service import (
    mark_cancellation_requested,
    mark_cancellation_retry,
    stage_account_deletion,
)
from app.services.account_export_service import build_account_export
from app.services.alert_service import dispatch_operational_alert
from app.services.billing_service import BillingProviderError, cancel_stripe_subscription
from app.services.storage_outbox_service import (
    enqueue_photo_deletions,
    process_storage_deletion_outbox,
)

router = APIRouter(prefix="/api/v1/users")


@router.get("/me", response_model=UserRead)
def current_user(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserRead)
def update_current_user(
    payload: UserProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me/export")
def export_account_data(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    return build_account_export(user, db)


@router.delete("/me", status_code=202, response_model=None)
def delete_account(
    payload: AccountDeleteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response | dict:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(403, "Неверный пароль")
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if (
        subscription
        and subscription.provider == "stripe"
        and subscription.provider_subscription_id
        and subscription.status not in {"canceled", "incomplete_expired"}
    ):
        deletion_request = stage_account_deletion(
            db,
            user,
            subscription.provider_subscription_id,
        )
        mark_cancellation_requested(db, deletion_request)
        try:
            cancel_stripe_subscription(subscription.provider_subscription_id, user.id)
        except BillingProviderError as exc:
            mark_cancellation_retry(db, deletion_request, exc)
            dispatch_operational_alert(
                "account_deletion_subscription_cancel_failed",
                details={"user_id": user.id, "attempts": deletion_request.attempts},
            )
        return {
            "status": "awaiting_subscription_cancellation",
            "account_blocked": True,
        }
    photos_to_delete = list(
        db.scalars(select(PlantPhoto).join(Plant).join(Garden).where(Garden.user_id == user.id))
    )
    enqueue_photo_deletions(db, photos_to_delete)
    db.delete(user)
    db.commit()
    process_storage_deletion_outbox(db)
    return Response(status_code=204)
