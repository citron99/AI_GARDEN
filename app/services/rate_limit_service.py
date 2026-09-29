import hashlib
import hmac
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException

from app.config import settings

_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def _key(scope: str, identifier: str) -> str:
    digest = hmac.new(settings.ai_safety_secret.encode(), identifier.casefold().encode(), hashlib.sha256).hexdigest()
    return f"rate:{scope}:{digest}"


def enforce_rate_limit(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window: int,
    unavailable_detail: str = "Защита от злоупотреблений временно недоступна",
) -> None:
    key = _key(scope, identifier)
    if settings.diagnosis_execution_mode == "celery":
        try:
            from redis import Redis

            client = Redis.from_url(settings.celery_broker_url, socket_connect_timeout=1, socket_timeout=1)
            count = client.incr(key)
            if count == 1:
                client.expire(key, window)
            if count > limit:
                raise HTTPException(429, "Слишком много попыток. Повторите позже")
            return
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, unavailable_detail) from exc
    now = time.monotonic()
    with _lock:
        values = _attempts[key]
        while values and values[0] <= now - window:
            values.popleft()
        if len(values) >= limit:
            raise HTTPException(429, "Слишком много попыток. Повторите позже")
        values.append(now)


def enforce_auth_rate_limit(scope: str, identifier: str) -> None:
    enforce_rate_limit(
        scope,
        identifier,
        limit=settings.auth_rate_limit_attempts,
        window=settings.auth_rate_limit_window_seconds,
        unavailable_detail="Защита авторизации временно недоступна",
    )


def reset_local_rate_limits() -> None:
    """Test/development helper; production limits live in Redis."""
    with _lock:
        _attempts.clear()
