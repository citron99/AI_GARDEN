import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from threading import Lock, Thread
from urllib.request import Request, urlopen

from app.config import settings

logger = logging.getLogger(__name__)
_lock = Lock()
_last_sent: dict[str, float] = {}


def _payload(event: str, severity: str, details: dict[str, str | int | float | bool | None]) -> bytes:
    return json.dumps({
        "event": event,
        "severity": severity,
        "occurred_at": datetime.now(UTC).isoformat(),
        "environment": settings.environment,
        "details": details,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _signature(body: bytes) -> str:
    return hmac.new((settings.alert_webhook_secret or "").encode("utf-8"), body, hashlib.sha256).hexdigest()


def _send(body: bytes) -> None:
    request = Request(  # noqa: S310 -- схема URL вебхука контролируется конфигурацией (в production обязателен HTTPS)
        settings.alert_webhook_url or "",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AI-Garden-Signature": f"sha256={_signature(body)}",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 -- см. noqa выше: URL вебхука из доверенной конфигурации
            if response.status >= 300:
                raise RuntimeError(f"alert webhook returned {response.status}")
    except Exception:
        logger.exception("operational alert delivery failed")


def dispatch_operational_alert(
    event: str,
    *,
    severity: str = "error",
    details: dict[str, str | int | float | bool | None] | None = None,
) -> bool:
    if not settings.alert_webhook_url:
        return False
    now = time.monotonic()
    with _lock:
        previous = _last_sent.get(event, 0)
        if now - previous < settings.alert_min_interval_seconds:
            return False
        _last_sent[event] = now
    body = _payload(event, severity, details or {})
    Thread(target=_send, args=(body,), daemon=True, name="ai-garden-alert").start()
    return True


def reset_alert_rate_limits() -> None:
    with _lock:
        _last_sent.clear()
