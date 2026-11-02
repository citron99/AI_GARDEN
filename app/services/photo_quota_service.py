"""Квота хранения фотографий пользователя."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Garden, Plant, PlantPhoto, User


def reserve_photo_storage(db: Session, user_id: int, incoming_bytes: int) -> None:
    # Блокировка строки пользователя сериализует параллельные загрузки: без неё
    # две загрузки могут вместе превысить квоту (на SQLite блокировка — no-op).
    # skip_locked недопустим: второй запрос должен дождаться первого и пересчитать сумму.
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    used_storage = (
        db.scalar(
            select(func.coalesce(func.sum(PlantPhoto.size_bytes), 0))
            .join(Plant)
            .join(Garden)
            .where(Garden.user_id == user_id)
        )
        or 0
    )
    quota_bytes = settings.user_storage_quota_mb * 1024 * 1024
    if used_storage + incoming_bytes > quota_bytes:
        raise HTTPException(413, "Квота хранения фотографий исчерпана")
