"""Расчёт следующего повтора напоминания в локальном часовом поясе сада."""

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Reminder


def aware_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def next_occurrence(due_at: datetime, recurrence: str, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = aware_utc(due_at).astimezone(zone)
    if recurrence in {"daily", "weekly"}:
        days = 1 if recurrence == "daily" else 7
        return (local + timedelta(days=days)).astimezone(UTC)
    year = local.year + (1 if local.month == 12 else 0)
    month = 1 if local.month == 12 else local.month + 1
    day = min(local.day, monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(UTC)


def clone_for_recurrence(reminder: Reminder) -> Reminder:
    return Reminder(
        plant_id=reminder.plant_id,
        kind=reminder.kind,
        title=reminder.title,
        due_at=next_occurrence(reminder.due_at, reminder.recurrence, reminder.timezone),
        notes=reminder.notes,
        recurrence=reminder.recurrence,
        timezone=reminder.timezone,
        preferred_channel=reminder.preferred_channel,
    )
