import hashlib
import hmac
import json
import time
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import (
    AccountDeletionRequest,
    BillingEvent,
    StorageDeletionOutbox,
    Subscription,
    User,
)


def register(client, email="billing@example.com"):
    response = client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "Плательщик",
        "password": "strong-password",
        "region": "Riga",
    })
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def signed_payload(event: dict, secret: str, timestamp: int | None = None) -> tuple[bytes, str]:
    timestamp = timestamp or int(time.time())
    payload = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={timestamp},v1={signature}"


def image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color=(46, 139, 87)).save(output, format="JPEG")
    return output.getvalue()


def test_billing_disabled_has_safe_free_plan(client):
    headers = register(client)
    plans = client.get("/api/v1/billing/plans")
    assert plans.status_code == 200
    assert [plan["code"] for plan in plans.json()] == ["free", "pro"]
    assert plans.json()[1]["checkout_available"] is False
    subscription = client.get("/api/v1/billing/subscription", headers=headers).json()
    assert subscription["plan"] == "free"
    assert subscription["billing_enabled"] is False
    assert client.post(
        "/api/v1/billing/checkout", headers=headers, json={"plan": "pro"}
    ).status_code == 503
    assert client.post("/api/v1/billing/stripe/webhook", content=b"{}").status_code == 404


def test_signed_webhook_activates_subscription_once(client, monkeypatch):
    headers = register(client, "stripe-user@example.com")
    user_id = client.get("/api/v1/users/me", headers=headers).json()["id"]
    secret = "whsec_test-signing-secret"
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not-called")
    monkeypatch.setattr(settings, "stripe_price_pro_monthly", "price_pro_test")

    event = {
        "id": "evt_subscription_active",
        "type": "customer.subscription.updated",
        "created": int(time.time()),
        "data": {"object": {
            "id": "sub_123",
            "customer": "cus_123",
            "metadata": {"user_id": str(user_id)},
            "status": "active",
            "current_period_end": int(time.time()) + 86400,
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_pro_test"}}]},
        }},
    }
    payload, signature = signed_payload(event, secret)
    response = client.post(
        "/api/v1/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": True, "new_event": True}
    duplicate = client.post(
        "/api/v1/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )
    assert duplicate.json() == {"received": True, "new_event": False}
    status = client.get("/api/v1/billing/subscription", headers=headers).json()
    assert status["plan"] == "pro"
    assert status["status"] == "active"
    assert status["can_manage"] is True
    monkeypatch.setattr(settings, "free_diagnoses_per_month", 0)
    monkeypatch.setattr(settings, "pro_diagnoses_per_month", 1)
    garden = client.post("/api/v1/gardens", headers=headers, json={"name": "Pro сад"}).json()
    plant = client.post("/api/v1/plants", headers=headers, json={
        "garden_id": garden["id"], "name": "Томат", "growing_place": "greenhouse",
    }).json()
    photo = client.post(
        f"/api/v1/plants/{plant['id']}/photos", headers=headers,
        files=[("files", ("leaf.jpg", image_bytes(), "image/jpeg"))],
    ).json()[0]
    diagnosis_payload = {
        "plant_id": plant["id"],
        "symptoms": "Нижние листья начали желтеть неделю назад",
        "damaged_part": "leaf",
        "photo_ids": [photo["id"]],
    }
    assert client.post("/api/v1/diagnoses", headers=headers, json=diagnosis_payload).status_code == 202
    assert client.post("/api/v1/diagnoses", headers=headers, json=diagnosis_payload).status_code == 429
    with SessionLocal() as db:
        subscription = db.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert subscription.provider_customer_id == "cus_123"
        assert db.query(BillingEvent).count() == 1

    bad = client.post(
        "/api/v1/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={int(time.time())},v1=bad"},
    )
    assert bad.status_code == 400

    older_event = {
        **event,
        "id": "evt_subscription_older",
        "created": event["created"] - 60,
        "data": {"object": {**event["data"]["object"], "status": "past_due"}},
    }
    older_payload, older_signature = signed_payload(older_event, secret)
    assert client.post(
        "/api/v1/billing/stripe/webhook", content=older_payload,
        headers={"Stripe-Signature": older_signature},
    ).status_code == 200
    assert client.get("/api/v1/billing/subscription", headers=headers).json()["status"] == "active"


def test_webhook_rejects_old_signature_and_unknown_price(client, monkeypatch):
    headers = register(client, "old-signature@example.com")
    user_id = client.get("/api/v1/users/me", headers=headers).json()["id"]
    secret = "whsec_another-test-secret"
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr(settings, "stripe_price_pro_monthly", "price_expected")
    event = {
        "id": "evt_wrong_price",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_wrong",
            "customer": "cus_wrong",
            "metadata": {"user_id": str(user_id)},
            "status": "active",
            "items": {"data": [{"price": {"id": "price_other"}}]},
        }},
    }
    old_payload, old_signature = signed_payload(event, secret, int(time.time()) - 1000)
    assert client.post(
        "/api/v1/billing/stripe/webhook", content=old_payload,
        headers={"Stripe-Signature": old_signature},
    ).status_code == 400
    payload, signature = signed_payload(event, secret)
    assert client.post(
        "/api/v1/billing/stripe/webhook", content=payload,
        headers={"Stripe-Signature": signature},
    ).status_code == 200
    assert client.get("/api/v1/billing/subscription", headers=headers).json()["status"] == "unsupported_price"


def test_checkout_and_portal_use_server_side_subscription_data(client, monkeypatch):
    import app.billing as billing_module

    headers = register(client, "checkout@example.com")
    user_id = client.get("/api/v1/users/me", headers=headers).json()["id"]
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "stripe_price_pro_monthly", "price_server_controlled")
    monkeypatch.setattr(
        billing_module,
        "create_checkout_session",
        lambda user, subscription: "https://checkout.stripe.test/session",
    )
    checkout = client.post(
        "/api/v1/billing/checkout",
        headers=headers,
        json={"plan": "pro", "price_id": "price_attacker_controlled"},
    )
    assert checkout.status_code == 200
    assert checkout.json()["url"].startswith("https://")
    assert client.post(
        "/api/v1/billing/checkout", headers=headers, json={"plan": "enterprise"}
    ).status_code == 422

    with SessionLocal() as db:
        db.add(Subscription(
            user_id=user_id,
            plan="pro",
            provider="stripe",
            provider_customer_id="cus_portal",
            provider_subscription_id="sub_portal",
            status="active",
        ))
        db.commit()
    assert client.post(
        "/api/v1/billing/checkout", headers=headers, json={"plan": "pro"}
    ).status_code == 409
    monkeypatch.setattr(
        billing_module,
        "create_portal_session",
        lambda subscription: "https://billing.stripe.test/portal",
    )
    assert client.post("/api/v1/billing/portal", headers=headers).json()["url"].startswith("https://")


def test_paid_account_deletion_waits_for_stripe_webhook(client, monkeypatch):
    import app.main as main_module

    headers = register(client, "delete-paid@example.com")
    user_id = client.get("/api/v1/users/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        db.add(Subscription(
            user_id=user_id,
            plan="pro",
            provider="stripe",
            provider_customer_id="cus_delete",
            provider_subscription_id="sub_delete",
            status="active",
        ))
        db.commit()

    cancellations = []
    monkeypatch.setattr(
        main_module,
        "cancel_stripe_subscription",
        lambda subscription_id, deleted_user_id: cancellations.append(
            (subscription_id, deleted_user_id)
        ),
    )
    requested = client.request(
        "DELETE", "/api/v1/users/me", headers=headers,
        json={"password": "strong-password"},
    )
    assert requested.status_code == 202
    assert requested.json()["status"] == "awaiting_subscription_cancellation"
    assert cancellations == [("sub_delete", user_id)]
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    with SessionLocal() as db:
        assert db.get(User, user_id) is not None
        deletion = db.scalar(select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user_id,
        ))
        assert deletion.status == "awaiting_webhook"
        assert deletion.attempts == 1

    secret = "whsec_account-deletion-test"
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr(settings, "stripe_price_pro_monthly", "price_pro_test")
    event = {
        "id": "evt_subscription_deleted",
        "type": "customer.subscription.deleted",
        "created": int(time.time()),
        "data": {"object": {
            "id": "sub_delete",
            "customer": "cus_delete",
            "status": "canceled",
            "items": {"data": [{"price": {"id": "price_pro_test"}}]},
        }},
    }
    payload, signature = signed_payload(event, secret)
    webhook = client.post(
        "/api/v1/billing/stripe/webhook", content=payload,
        headers={"Stripe-Signature": signature},
    )
    assert webhook.status_code == 200
    with SessionLocal() as db:
        assert db.get(User, user_id) is None
        assert db.scalar(select(StorageDeletionOutbox.id)) is None
