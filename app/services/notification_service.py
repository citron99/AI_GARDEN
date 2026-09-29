from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Garden, Plant, Reminder, TelegramAccount, User, UserNotification
from app.services.seasonal_service import seasonal_calendar_items
from app.services.telegram_service import TelegramServiceError, send_message
from app.services.weather_service import WeatherServiceError, weather_service


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _add(
    db: Session,
    existing: set[str],
    *,
    user_id: int,
    kind: str,
    title: str,
    body: str,
    event_at: datetime,
    key: str,
    delivery_channel: str = "both",
) -> bool:
    if key in existing:
        return False
    db.add(UserNotification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        event_at=event_at,
        deduplication_key=key,
        delivery_channel=delivery_channel,
    ))
    existing.add(key)
    return True


def generate_due_notifications(
    db: Session,
    *,
    now: datetime | None = None,
    include_weather: bool = True,
) -> int:
    now = _aware(now or datetime.now(UTC)).astimezone(UTC)
    end = now + timedelta(hours=24)
    existing = set(db.scalars(select(UserNotification.deduplication_key)))
    created = 0

    reminder_rows = db.execute(
        select(Reminder, Plant, User)
        .join(Plant, Plant.id == Reminder.plant_id)
        .join(Garden, Garden.id == Plant.garden_id)
        .join(User, User.id == Garden.user_id)
        .where(Reminder.completed_at.is_(None), Reminder.due_at >= now, Reminder.due_at < end)
    ).all()
    for reminder, plant, user in reminder_rows:
        language = user.language if user.language in {"ru", "lv", "en"} else "ru"
        title = {"ru": "Скоро задача по уходу", "lv": "Drīz kopšanas uzdevums", "en": "Care task due soon"}[language]
        body = {
            "ru": f"{plant.name}: {reminder.title}",
            "lv": f"{plant.name}: {reminder.title}",
            "en": f"{plant.name}: {reminder.title}",
        }[language]
        created += _add(db, existing, user_id=user.id, kind="reminder", title=title, body=body,
                        event_at=_aware(reminder.due_at), key=f"reminder:{reminder.id}",
                        delivery_channel=reminder.preferred_channel)

    users = list(db.scalars(select(User).order_by(User.id)))
    for user in users:
        gardens = list(db.scalars(select(Garden).where(Garden.user_id == user.id).order_by(Garden.id)))
        for item in seasonal_calendar_items(gardens, user, now, end):
            created += _add(
                db, existing, user_id=user.id, kind="seasonal_task", title=item["title"],
                body=f"{item['garden_name']}: {item['description']}", event_at=item["starts_at"],
                key=str(item["reference_id"]),
            )
        if not include_weather:
            continue
        for garden in gardens[:20]:
            location = garden.location or user.region
            if not location:
                continue
            try:
                forecast = weather_service.get_forecast(location, user.language)
            except WeatherServiceError:
                continue
            try:
                zone = ZoneInfo(forecast.timezone)
            except ZoneInfoNotFoundError:
                zone = UTC
            for warning in forecast.warnings:
                event_at = datetime.combine(warning.date, time(hour=9), tzinfo=zone).astimezone(UTC)
                if now <= event_at < end:
                    created += _add(
                        db, existing, user_id=user.id, kind="weather_warning", title=warning.title,
                        body=f"{garden.name}: {warning.advice}", event_at=event_at,
                        key=f"weather:{garden.id}:{warning.date}:{warning.kind}",
                    )
    db.commit()
    return created


def _pending_notifications_query(now: datetime):
    return (
        select(UserNotification, TelegramAccount)
        .join(TelegramAccount, TelegramAccount.user_id == UserNotification.user_id)
        .where(
            TelegramAccount.active.is_(True),
            UserNotification.telegram_sent_at.is_(None),
            UserNotification.event_at < now + timedelta(hours=24),
            UserNotification.delivery_attempts < 5,
            UserNotification.delivery_channel.in_(("telegram", "both")),
        )
        .order_by(UserNotification.created_at)
        .limit(200)
        # Параллельный воркер пропускает уже заблокированные строки,
        # чтобы не отправить одно уведомление дважды (на SQLite — no-op).
        .with_for_update(skip_locked=True)
    )


def deliver_pending_telegram_notifications(db: Session, *, now: datetime | None = None) -> tuple[int, int]:
    now = _aware(now or datetime.now(UTC)).astimezone(UTC)
    rows = db.execute(_pending_notifications_query(now)).all()
    sent = failed = 0
    for notification, account in rows:
        notification.delivery_attempts += 1
        try:
            send_message(account.chat_id, f"{notification.title}\n{notification.body}")
        except TelegramServiceError as exc:
            notification.last_delivery_error = str(exc)[:300]
            failed += 1
        else:
            notification.telegram_sent_at = now
            notification.last_delivery_error = None
            sent += 1
    db.commit()
    return sent, failed
