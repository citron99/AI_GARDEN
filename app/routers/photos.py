import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Garden, Plant, PlantPhoto, User
from app.ownership import owned_plant
from app.schemas import PhotoRead
from app.services.image_service import (
    ImageValidationError,
    SanitizedImage,
    validate_and_sanitize_image,
)
from app.services.photo_quota_service import reserve_photo_storage
from app.services.rate_limit_service import enforce_rate_limit
from app.services.storage_outbox_service import (
    enqueue_photo_deletions,
    process_storage_deletion_outbox,
)
from app.services.storage_service import StorageError, delete_photo, photo_download, store_photo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

_ALLOWED_UPLOAD_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _owned_photo(photo_id: int, user: User, db: Session) -> PlantPhoto:
    photo = db.scalar(
        select(PlantPhoto)
        .join(Plant)
        .join(Garden)
        .where(PlantPhoto.id == photo_id, Garden.user_id == user.id)
    )
    if not photo:
        raise HTTPException(404, "Фотография не найдена")
    return photo


@router.post("/plants/{plant_id}/photos", response_model=list[PhotoRead], status_code=201)
async def upload_photos(
    plant_id: int,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PlantPhoto]:
    enforce_rate_limit(
        "photo_upload",
        str(user.id),
        limit=settings.upload_rate_limit_attempts,
        window=settings.upload_rate_limit_window_seconds,
    )
    owned_plant(plant_id, user, db)
    if not 1 <= len(files) <= 5:
        raise HTTPException(400, "Загрузите от 1 до 5 фотографий")
    prepared: list[SanitizedImage] = []
    for upload in files:
        if upload.content_type not in _ALLOWED_UPLOAD_TYPES:
            raise HTTPException(415, "Поддерживаются только JPEG, PNG и WebP")
        content = await upload.read(settings.max_image_size_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_image_size_mb * 1024 * 1024:
            raise HTTPException(413, f"Размер файла превышает {settings.max_image_size_mb} МБ")
        try:
            prepared.append(validate_and_sanitize_image(content, upload.content_type))
        except ImageValidationError as exc:
            raise HTTPException(exc.status_code, exc.detail)

    reserve_photo_storage(db, user.id, sum(len(image.content) for image in prepared))

    saved: list[PlantPhoto] = []
    stored: list[PlantPhoto] = []
    try:
        for image in prepared:
            storage_key, file_path = store_photo(
                plant_id, image.content, image.extension, image.content_type
            )
            photo = PlantPhoto(
                plant_id=plant_id,
                storage_key=storage_key,
                file_path=file_path,
                content_type=image.content_type,
                size_bytes=len(image.content),
            )
            db.add(photo)
            saved.append(photo)
            stored.append(photo)
        db.commit()
    except Exception:
        db.rollback()
        for photo in stored:
            try:
                delete_photo(photo)
            except StorageError:
                logger.exception("Could not rollback stored photo")
        raise
    for photo in saved:
        db.refresh(photo)
    return saved


@router.get("/photos/{photo_id}")
def get_photo(
    photo_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    photo = _owned_photo(photo_id, user, db)
    try:
        target = photo_download(photo)
    except StorageError:
        raise HTTPException(404, "Файл фотографии не найден")
    if isinstance(target, str):
        return RedirectResponse(
            target, status_code=307, headers={"Cache-Control": "private, no-store"}
        )
    return FileResponse(
        target,
        media_type=photo.content_type,
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/photos/{photo_id}", status_code=204)
def delete_owned_photo(
    photo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    photo = _owned_photo(photo_id, user, db)
    enqueue_photo_deletions(db, [photo])
    db.delete(photo)
    db.commit()
    process_storage_deletion_outbox(db)
    return Response(status_code=204)
