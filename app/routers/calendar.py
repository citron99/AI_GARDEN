from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as datetime_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import CareEvent, Garden, Plant, Reminder, User
from app.schemas import CalendarItemRead
from app.services.seasonal_service import seasonal_calendar_items
from app.services.weather_service import WeatherServiceError, weather_service

router = APIRouter()

_WARNING_LOCAL_HOUR = 9


def _weather_warning_items(
    gardens: list[Garden], user: User, start_utc: datetime, end_utc: datetime
) -> tuple[list[dict], int, int]:
    attempted = 0
    succeeded = 0
    items: list[dict] = []
    for garden in gardens:
        location = garden.location or user.region
        if not location:
            continue
        attempted += 1
        try:
            forecast = weather_service.get_forecast(location, user.language)
        except WeatherServiceError:
            continue
        succeeded += 1
        try:
            forecast_timezone: tzinfo = ZoneInfo(forecast.timezone)
        except ZoneInfoNotFoundError:
            forecast_timezone = UTC
        for warning in forecast.warnings:
            warning_at = datetime.combine(
                warning.date, datetime_time(hour=_WARNING_LOCAL_HOUR), tzinfo=forecast_timezone
            )
            warning_utc = warning_at.astimezone(UTC)
            if start_utc <= warning_utc < end_utc:
                items.append(
                    {
                        "item_type": "weather_warning",
                        "reference_id": f"weather:{garden.id}:{warning.date}:{warning.kind}",
                        "garden_id": garden.id,
                        "garden_name": garden.name,
                        "event_type": warning.kind,
                        "title": warning.title,
                        "severity": warning.severity,
                        "description": warning.advice,
                        "starts_at": warning_at,
                        "completed": False,
                    }
                )
    return items, attempted, succeeded


def _sort_key(item: dict) -> datetime:
    value = item["starts_at"]
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.get("/api/v1/calendar", response_model=list[CalendarItemRead])
def calendar_items(
    start: datetime,
    end: datetime,
    include_weather: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
    ):
        raise HTTPException(422, "Период календаря должен включать часовой пояс")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if start_utc >= end_utc:
        raise HTTPException(422, "Начало периода должно быть раньше окончания")
    if end_utc - start_utc > timedelta(days=366):
        raise HTTPException(422, "Период календаря не может превышать 366 дней")

    reminders = db.execute(
        select(Reminder, Plant.name)
        .join(Plant)
        .join(Garden)
        .where(Garden.user_id == user.id, Reminder.due_at >= start_utc, Reminder.due_at < end_utc)
    ).all()
    care_events = db.execute(
        select(CareEvent, Plant.name)
        .join(Plant)
        .join(Garden)
        .where(
            Garden.user_id == user.id,
            CareEvent.occurred_at >= start_utc,
            CareEvent.occurred_at < end_utc,
        )
    ).all()
    items = [
        {
            "item_type": "reminder",
            "reference_id": reminder.id,
            "plant_id": reminder.plant_id,
            "plant_name": plant_name,
            "event_type": reminder.kind,
            "title": reminder.title,
            "starts_at": reminder.due_at,
            "completed": reminder.completed,
        }
        for reminder, plant_name in reminders
    ]
    items.extend(
        {
            "item_type": "care_event",
            "reference_id": event.id,
            "plant_id": event.plant_id,
            "plant_name": plant_name,
            "event_type": event.event_type,
            "title": event.notes or event.product or event.event_type,
            "starts_at": event.occurred_at,
            "completed": True,
        }
        for event, plant_name in care_events
    )
    gardens = list(
        db.scalars(select(Garden).where(Garden.user_id == user.id).order_by(Garden.id).limit(50))
    )
    items.extend(seasonal_calendar_items(gardens, user, start_utc, end_utc))
    if include_weather:
        weather_items, attempted, succeeded = _weather_warning_items(
            gardens[:20], user, start_utc, end_utc
        )
        if attempted and not succeeded:
            raise HTTPException(503, "Погодный сервис временно недоступен")
        items.extend(weather_items)

    return sorted(items, key=_sort_key)
