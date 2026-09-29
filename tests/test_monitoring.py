import hashlib
import hmac
import json

from app.config import settings
from app.services import alert_service


def test_operational_alert_is_signed_rate_limited_and_contains_no_request_payload(monkeypatch):
    secret = "alert-test-secret-that-is-longer-than-thirty-two"
    monkeypatch.setattr(settings, "alert_webhook_url", "https://alerts.example.test/hook")
    monkeypatch.setattr(settings, "alert_webhook_secret", secret)
    monkeypatch.setattr(settings, "alert_min_interval_seconds", 300)
    captured: list[bytes] = []

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(alert_service, "Thread", ImmediateThread)
    monkeypatch.setattr(alert_service, "_send", captured.append)
    assert alert_service.dispatch_operational_alert(
        "api_unhandled_error", details={"request_id": "safe-id", "route": "/api/v1/example"},
    ) is True
    assert alert_service.dispatch_operational_alert("api_unhandled_error") is False
    body = captured[0]
    payload = json.loads(body)
    assert payload["details"] == {"request_id": "safe-id", "route": "/api/v1/example"}
    assert "symptoms" not in payload
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert alert_service._signature(body) == expected
