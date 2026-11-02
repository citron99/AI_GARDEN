from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User, UserNotification
from app.schemas import NotificationRead

router = APIRouter(prefix="/api/v1/notifications")


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    unread_only: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UserNotification]:
    query = select(UserNotification).where(UserNotification.user_id == user.id)
    if unread_only:
        query = query.where(UserNotification.read_at.is_(None))
    return list(
        db.scalars(query.order_by(UserNotification.event_at.desc()).offset(offset).limit(limit))
    )


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_notification_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserNotification:
    item = db.scalar(
        select(UserNotification).where(
            UserNotification.id == notification_id, UserNotification.user_id == user.id
        )
    )
    if not item:
        raise HTTPException(404, "Уведомление не найдено")
    item.read_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return item
