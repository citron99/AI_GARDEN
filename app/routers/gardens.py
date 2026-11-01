from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Garden, Plant, PlantPhoto, User
from app.ownership import owned_garden
from app.schemas import GardenCreate, GardenRead, GardenUpdate, WeatherForecastRead
from app.services.storage_outbox_service import (
    enqueue_photo_deletions,
    process_storage_deletion_outbox,
)
from app.services.weather_service import WeatherServiceError, weather_service

router = APIRouter(prefix="/api/v1/gardens")


@router.post("", response_model=GardenRead, status_code=201)
def create_garden(
    payload: GardenCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Garden:
    garden = Garden(user_id=user.id, **payload.model_dump())
    db.add(garden)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Сад с таким названием уже существует")
    db.refresh(garden)
    return garden


@router.get("", response_model=list[GardenRead])
def list_gardens(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Garden]:
    return list(
        db.scalars(
            select(Garden)
            .where(Garden.user_id == user.id)
            .order_by(Garden.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.patch("/{garden_id}", response_model=GardenRead)
def update_garden(
    garden_id: int,
    payload: GardenUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Garden:
    garden = owned_garden(garden_id, user, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(garden, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Сад с таким названием уже существует")
    db.refresh(garden)
    return garden


@router.delete("/{garden_id}", status_code=204)
def delete_garden(
    garden_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    garden = owned_garden(garden_id, user, db)
    photos = list(db.scalars(select(PlantPhoto).join(Plant).where(Plant.garden_id == garden.id)))
    enqueue_photo_deletions(db, photos)
    db.delete(garden)
    db.commit()
    process_storage_deletion_outbox(db)
    return Response(status_code=204)


@router.get("/{garden_id}/weather", response_model=WeatherForecastRead)
def garden_weather(
    garden_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WeatherForecastRead:
    garden = owned_garden(garden_id, user, db)
    location = garden.location or user.region
    if not location:
        raise HTTPException(422, "Укажите местоположение сада или регион профиля")
    try:
        return weather_service.get_forecast(location, user.language)
    except WeatherServiceError as exc:
        raise HTTPException(503, str(exc)) from exc
