import pytest
from fastapi import HTTPException
from redis import Redis

from app.config import settings
from app.services.rate_limit_service import enforce_rate_limit


class FakeRedis:
    """Счётчик с поведением Redis: INCR без предварительной установки срока
    жизни создаёт ключ навсегда."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int | None] = {}

    def set(self, key: str, value: int, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.counts:
            return False
        self.counts[key] = int(value)
        self.ttls[key] = ex
        return True

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        self.ttls.setdefault(key, None)
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds


@pytest.fixture
def redis_mode(monkeypatch):
    monkeypatch.setattr(settings, "diagnosis_execution_mode", "celery")
    fake = FakeRedis()
    monkeypatch.setattr(Redis, "from_url", staticmethod(lambda url, **kwargs: fake))
    return fake


def test_counter_is_created_with_ttl(redis_mode):
    enforce_rate_limit("auth-login", "user@example.com", limit=3, window=900)

    (key,) = redis_mode.counts
    assert redis_mode.counts[key] == 1
    assert redis_mode.ttls[key] == 900


def test_broken_expire_cannot_leave_a_permanent_block(redis_mode):
    """Обрыв между увеличением счётчика и простановкой TTL раньше оставлял ключ
    без срока: лимит больше не сбрасывался никогда."""

    def drop_connection(key: str) -> int:
        raise ConnectionError("Connection closed")

    redis_mode.expire = drop_connection
    enforce_rate_limit("auth-login", "user@example.com", limit=3, window=900)

    (key,) = redis_mode.counts
    assert redis_mode.ttls[key] == 900


def test_window_is_not_extended_by_new_attempts(redis_mode):
    for _ in range(3):
        enforce_rate_limit("auth-login", "user@example.com", limit=3, window=60)

    (key,) = redis_mode.counts
    assert redis_mode.counts[key] == 3
    assert redis_mode.ttls[key] == 60

    with pytest.raises(HTTPException) as failure:
        enforce_rate_limit("auth-login", "user@example.com", limit=3, window=60)
    assert failure.value.status_code == 429


def test_unavailable_redis_fails_closed(redis_mode, monkeypatch):
    def refuse(url, **kwargs):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(Redis, "from_url", staticmethod(refuse))

    with pytest.raises(HTTPException) as failure:
        enforce_rate_limit("auth-login", "user@example.com", limit=3, window=60)
    assert failure.value.status_code == 503
