from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import CareEvent, Reminder, User
from app.ownership import owned_care_event, owned_plant, owned_reminder
from app.schemas import (
    CareEventCreate,
    CareEventRead,
    CareEventUpdate,
    ReminderCreate,
    ReminderRead,
    ReminderUpdate,
)
from app.services.reminder_schedule_service import aware_utc, clone_for_recurrence

router = APIRouter(prefix="/api/v1")


@router.post("/plants/{plant_id}/care-events", response_model=CareEventRead, status_code=201)
def create_care_event(
    plant_id: int,
    payload: CareEventCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CareEvent:
    owned_plant(plant_id, user, db)
    values = payload.model_dump()
    values["occurred_at"] = payload.occurred_at.astimezone(UTC)
    event = CareEvent(plant_id=plant_id, **values)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/plants/{plant_id}/care-events", response_model=list[CareEventRead])
def list_care_events(
    plant_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CareEvent]:
    owned_plant(plant_id, user, db)
    query = select(CareEvent).where(CareEvent.plant_id == plant_id)
    if start is not None:
        query = query.where(CareEvent.occurred_at >= start)
    if end is not None:
        query = query.where(CareEvent.occurred_at <= end)
    return list(
        db.scalars(
            query.order_by(CareEvent.occurred_at.desc(), CareEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.patch("/care-events/{event_id}", response_model=CareEventRead)
def update_care_event(
    event_id: int,
    payload: CareEventUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CareEvent:
    event = owned_care_event(event_id, user, db)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("occurred_at") is not None:
        changes["occurred_at"] = changes["occurred_at"].astimezone(UTC)
    resulting_amount = changes.get("amount", event.amount)
    resulting_unit = changes.get("unit", event.unit)
    if resulting_unit and resulting_amount is None:
        raise HTTPException(422, "Единица измерения требует количества")
    for field, value in changes.items():
        setattr(event, field, value)
    db.commit()
    db.refresh(event)
    return event


@router.delete("/care-events/{event_id}", status_code=204)
def delete_care_event(
    event_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    event = owned_care_event(event_id, user, db)
    db.delete(event)
    db.commit()
    return Response(status_code=204)


@router.post("/plants/{plant_id}/reminders", response_model=ReminderRead, status_code=201)
def create_reminder(
    plant_id: int,
    payload: ReminderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Reminder:
    owned_plant(plant_id, user, db)
    values = payload.model_dump()
    values["due_at"] = payload.due_at.astimezone(UTC)
    reminder = Reminder(plant_id=plant_id, **values)
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


@router.get("/plants/{plant_id}/reminders", response_model=list[ReminderRead])
def list_reminders(
    plant_id: int,
    include_completed: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Reminder]:
    owned_plant(plant_id, user, db)
    query = select(Reminder).where(Reminder.plant_id == plant_id)
    if not include_completed:
        query = query.where(Reminder.completed_at.is_(None))
    return list(
        db.scalars(query.order_by(Reminder.due_at, Reminder.id).offset(offset).limit(limit))
    )


@router.patch("/reminders/{reminder_id}", response_model=ReminderRead)
def update_reminder(
    reminder_id: int,
    payload: ReminderUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Reminder:
    reminder = owned_reminder(reminder_id, user, db)
    changes = payload.model_dump(exclude_unset=True)
    completed = changes.pop("completed", None)
    snooze_minutes = changes.pop("snooze_minutes", None)
    skip_occurrence = changes.pop("skip_occurrence", None)
    if changes.get("due_at") is not None:
        changes["due_at"] = changes["due_at"].astimezone(UTC)
    for field, value in changes.items():
        setattr(reminder, field, value)
    if snooze_minutes is not None:
        reminder.due_at = aware_utc(reminder.due_at) + timedelta(minutes=snooze_minutes)
        reminder.completed_at = None
    if skip_occurrence:
        completed = True
    if completed is not None:
        was_completed = reminder.completed_at is not None
        reminder.completed_at = datetime.now(UTC) if completed else None
        if completed and not was_completed and reminder.recurrence:
            db.add(clone_for_recurrence(reminder))
    db.commit()
    db.refresh(reminder)
    return reminder


@router.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(
    reminder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    reminder = owned_reminder(reminder_id, user, db)
    db.delete(reminder)
    db.commit()
    return Response(status_code=204)
