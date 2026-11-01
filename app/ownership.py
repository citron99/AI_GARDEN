"""Проверки принадлежности объектов текущему пользователю."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CareEvent, Diagnosis, Garden, Plant, Reminder, User


def owned_garden(garden_id: int, user: User, db: Session) -> Garden:
    garden = db.scalar(select(Garden).where(Garden.id == garden_id, Garden.user_id == user.id))
    if not garden:
        raise HTTPException(404, "Сад не найден")
    return garden


def owned_plant(plant_id: int, user: User, db: Session) -> Plant:
    plant = db.scalar(
        select(Plant).join(Garden).where(Plant.id == plant_id, Garden.user_id == user.id)
    )
    if not plant:
        raise HTTPException(404, "Растение не найдено")
    return plant


def owned_diagnosis(diagnosis_id: int, user: User, db: Session) -> Diagnosis:
    diagnosis = db.scalar(
        select(Diagnosis)
        .join(Plant)
        .join(Garden)
        .where(Diagnosis.id == diagnosis_id, Garden.user_id == user.id)
    )
    if not diagnosis:
        raise HTTPException(404, "Диагностика не найдена")
    return diagnosis


def owned_care_event(event_id: int, user: User, db: Session) -> CareEvent:
    event = db.scalar(
        select(CareEvent)
        .join(Plant)
        .join(Garden)
        .where(CareEvent.id == event_id, Garden.user_id == user.id)
    )
    if not event:
        raise HTTPException(404, "Запись ухода не найдена")
    return event


def owned_reminder(reminder_id: int, user: User, db: Session) -> Reminder:
    reminder = db.scalar(
        select(Reminder)
        .join(Plant)
        .join(Garden)
        .where(Reminder.id == reminder_id, Garden.user_id == user.id)
    )
    if not reminder:
        raise HTTPException(404, "Напоминание не найдено")
    return reminder
