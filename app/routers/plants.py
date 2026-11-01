from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Garden, Plant, PlantPhoto, User
from app.ownership import owned_garden, owned_plant
from app.schemas import PlantCreate, PlantRead, PlantUpdate
from app.services.storage_outbox_service import (
    enqueue_photo_deletions,
    process_storage_deletion_outbox,
)

router = APIRouter(prefix="/api/v1/plants")


@router.post("", response_model=PlantRead, status_code=201)
def create_plant(
    payload: PlantCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Plant:
    garden = db.get(Garden, payload.garden_id)
    if not garden or garden.user_id != user.id:
        raise HTTPException(404, "Сад не найден")
    plant = Plant(**payload.model_dump())
    db.add(plant)
    db.commit()
    db.refresh(plant)
    return plant


@router.get("", response_model=list[PlantRead])
def list_plants(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Plant]:
    return list(
        db.scalars(
            select(Plant)
            .join(Garden)
            .where(Garden.user_id == user.id)
            .order_by(Plant.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/{plant_id}", response_model=PlantRead)
def get_plant(
    plant_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Plant:
    return owned_plant(plant_id, user, db)


@router.patch("/{plant_id}", response_model=PlantRead)
def update_plant(
    plant_id: int,
    payload: PlantUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Plant:
    plant = owned_plant(plant_id, user, db)
    changes = payload.model_dump(exclude_unset=True)
    if "garden_id" in changes:
        owned_garden(changes["garden_id"], user, db)
    for field, value in changes.items():
        setattr(plant, field, value)
    db.commit()
    db.refresh(plant)
    return plant


@router.delete("/{plant_id}", status_code=204)
def delete_plant(
    plant_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    plant = owned_plant(plant_id, user, db)
    photos = list(db.scalars(select(PlantPhoto).where(PlantPhoto.plant_id == plant.id)))
    enqueue_photo_deletions(db, photos)
    db.delete(plant)
    db.commit()
    process_storage_deletion_outbox(db)
    return Response(status_code=204)
