from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.database import SessionLocal
from app.models import TelegramAccount, User, UserNotification
from app.services import notification_service


def test_pending_notification_is_delivered_to_telegram_once(client, monkeypatch):
    response = client.post("/api/v1/auth/register", json={
        "email": "proactive@example.com",
        "name": "Proactive",
        "password": "strong-password",
        "language": "en",
    })
    assert response.status_code == 201
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "proactive@example.com"))
        db.add(TelegramAccount(user_id=user.id, chat_id=123456, language="en", active=True))
        db.add(UserNotification(
            user_id=user.id,
            kind="seasonal_task",
            title="Seasonal task",
            body="Prepare frost protection",
            event_at=now + timedelta(hours=2),
            deduplication_key="test:seasonal:2026",
        ))
        db.commit()

    sent_messages = []
    monkeypatch.setattr(notification_service, "send_message", lambda chat_id, text: sent_messages.append((chat_id, text)))
    with SessionLocal() as db:
        assert notification_service.deliver_pending_telegram_notifications(db, now=now) == (1, 0)
        assert notification_service.deliver_pending_telegram_notifications(db, now=now) == (0, 0)
        item = db.scalar(select(UserNotification))
        assert item.telegram_sent_at is not None
        assert item.delivery_attempts == 1
    assert sent_messages == [(123456, "Seasonal task\nPrepare frost protection")]


def test_pending_delivery_query_locks_rows_on_postgres():
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    statement = notification_service._pending_notifications_query(now)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in compiled


def _jpeg_bytes():
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (32, 32), color=(46, 139, 87)).save(output, format="JPEG")
    return output.getvalue()


def _run_diagnosis(client, headers, plant_id):
    photo_id = client.post(
        f"/api/v1/plants/{plant_id}/photos",
        headers=headers,
        files=[("files", ("leaf.jpg", _jpeg_bytes(), "image/jpeg"))],
    ).json()[0]["id"]
    job = client.post(
        "/api/v1/diagnoses/async",
        headers=headers,
        json={
            "plant_id": plant_id,
            "symptoms": "Lower leaves are turning yellow",
            "damaged_part": "leaf",
            "photo_ids": [photo_id],
        },
    ).json()
    assert job["status"] == "succeeded"
    return job["diagnosis_id"]


def _register_with_telegram(client, email):
    response = client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "Gardener",
        "password": "strong-password",
        "language": "en",
    })
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        db.add(TelegramAccount(user_id=user.id, chat_id=424242, language="en", active=True))
        db.commit()
    garden = client.post("/api/v1/gardens", headers=headers,
                         json={"name": "Greenhouse", "kind": "greenhouse", "location": "Riga"})
    plant = client.post("/api/v1/plants", headers=headers, json={
        "garden_id": garden.json()["id"],
        "name": "Tomato",
        "taxon_id": "solanum.lycopersicum",
        "growing_place": "greenhouse",
        "region": "Riga",
    })
    assert plant.status_code == 201
    return headers, plant.json()["id"]


def test_diagnosis_ready_notification_once_and_with_degraded_warning(client, monkeypatch):
    from app.config import settings

    headers, plant_id = _register_with_telegram(client, "diag-ready@example.com")

    # Обычный результат: уведомление без деградационного предупреждения
    first_id = _run_diagnosis(client, headers, plant_id)
    with SessionLocal() as db:
        note = db.scalar(select(UserNotification).where(UserNotification.kind == "diagnosis_ready"))
        assert note is not None
        assert note.deduplication_key == f"diagnosis_ready:{first_id}"
        assert note.title == "Diagnosis ready"
        assert "Tomato" in note.body
        assert "temporarily unavailable" not in note.body

    # Ответ резервного текстового провайдера: предупреждение добавляется
    monkeypatch.setattr(settings, "deepseek_model", "mock-rule-based")
    second_id = _run_diagnosis(client, headers, plant_id)
    assert second_id != first_id
    with SessionLocal() as db:
        notes = db.scalars(select(UserNotification).where(
            UserNotification.kind == "diagnosis_ready",
            UserNotification.deduplication_key == f"diagnosis_ready:{second_id}",
        )).all()
        assert len(notes) == 1
        assert "temporarily unavailable" in notes[0].body
        assert "backup text-only provider" in notes[0].body

    # Текст уходит в Telegram как есть
    sent_messages = []
    monkeypatch.setattr(notification_service, "send_message",
                        lambda chat_id, text: sent_messages.append((chat_id, text)))
    now = datetime.now(UTC)
    with SessionLocal() as db:
        assert notification_service.deliver_pending_telegram_notifications(db, now=now) == (2, 0)
    assert len(sent_messages) == 2
    assert all(chat_id == 424242 for chat_id, _ in sent_messages)
    assert any("temporarily unavailable" in text for _, text in sent_messages)


def test_diagnosis_failed_notification_after_exhausted_retries(client, monkeypatch):
    import app.main as main_module
    from app.ai.base import AIProviderError

    headers, plant_id = _register_with_telegram(client, "diag-failed@example.com")
    photo_id = client.post(
        f"/api/v1/plants/{plant_id}/photos",
        headers=headers,
        files=[("files", ("leaf.jpg", _jpeg_bytes(), "image/jpeg"))],
    ).json()[0]["id"]

    class FailingGateway:
        model_name = "failing-model"
        prompt_version = "plant-diagnosis-v3"
        demo_mode = False

        def analyze(self, *args, **kwargs):
            raise AIProviderError(
                "unavailable",
                http_status=503,
                error_type="provider_unavailable",
                request_id="req_failed_notification",
                response_ms=100,
            )

    monkeypatch.setattr(main_module, "ai_gateway", FailingGateway())
    job = client.post(
        "/api/v1/diagnoses/async",
        headers=headers,
        json={
            "plant_id": plant_id,
            "symptoms": "Leaves are curling",
            "damaged_part": "leaf",
            "photo_ids": [photo_id],
        },
    ).json()
    assert job["status"] == "failed"

    with SessionLocal() as db:
        note = db.scalar(select(UserNotification).where(UserNotification.kind == "diagnosis_failed"))
        assert note is not None
        assert note.deduplication_key == f"diagnosis_failed:{job['id']}"
        assert note.title == "Diagnosis failed"
        assert "Tomato" in note.body
        assert "Please try again later." in note.body
        # Уведомления о готовности при провале нет
        assert db.scalar(select(UserNotification).where(UserNotification.kind == "diagnosis_ready")) is None

    sent_messages = []
    monkeypatch.setattr(notification_service, "send_message",
                        lambda chat_id, text: sent_messages.append((chat_id, text)))
    with SessionLocal() as db:
        assert notification_service.deliver_pending_telegram_notifications(db, now=datetime.now(UTC)) == (1, 0)
    assert len(sent_messages) == 1
    assert "Diagnosis failed" in sent_messages[0][1]