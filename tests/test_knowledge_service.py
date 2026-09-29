from datetime import UTC, date, datetime

import pytest

from app.models import KnowledgeChunk, KnowledgeSourceRecord
from app.services.knowledge_service import (
    KnowledgeSource,
    _query_embedding,
    _retrieve_from_database,
    _retrieve_static,
    _similarity_from_distance,
    clear_query_embedding_cache,
)


class CountingProvider:
    model_name = "counting-v1"

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("temporary embedding failure")
        return [[float(len(texts[0])), 1.0]]


def test_query_embedding_cache_does_not_retain_plain_query_or_cache_errors():
    clear_query_embedding_cache()
    provider = CountingProvider()
    assert _query_embedding(provider, "private symptom text") == [20.0, 1.0]
    assert _query_embedding(provider, "private symptom text") == [20.0, 1.0]
    assert provider.calls == 1

    clear_query_embedding_cache()
    failing = CountingProvider(fail=True)
    with pytest.raises(RuntimeError):
        _query_embedding(failing, "retry me")
    with pytest.raises(RuntimeError):
        _query_embedding(failing, "retry me")
    assert failing.calls == 2


def test_expired_static_knowledge_is_not_returned(monkeypatch):
    expired = KnowledgeSource(
        id="expired-source", title="Expired source", url="https://example.org/source",
        summary="An old source that must no longer be retrieved.", keywords=["water"],
        region="global", language=["en"], plant_types=["all"], problem_types=["watering"],
        last_verified_at=date(2020, 1, 1), next_review_at=date(2020, 2, 1),
        reviewed_by="test reviewer", review_role="editorial",
        usage_basis="linked_factual_summary", source_version="2020-01",
    )
    monkeypatch.setattr("app.services.knowledge_service._load_sources", lambda: [expired])
    assert _retrieve_static("water", 4, None, "en") == []


def test_similarity_from_distance_handles_exact_match_and_missing_row():
    assert _similarity_from_distance(0.0) == 1.0
    assert _similarity_from_distance(0.25) == 0.75
    assert _similarity_from_distance(None) == 0.0


class _FakeDialect:
    name = "postgresql"


class _FakeSession:
    """Заглушка сессии для pg-ветки: count источников и distance по каждому чанку."""

    def __init__(self, chunks, distances):
        self.bind = type("_FakeBind", (), {"dialect": _FakeDialect})()
        self._chunks = chunks
        self._distances = list(distances)
        self._calls = 0

    def scalar(self, statement):
        self._calls += 1
        return 1 if self._calls == 1 else self._distances.pop(0)

    def scalars(self, statement):
        return iter(self._chunks)


def _source_record(identifier: str) -> KnowledgeSourceRecord:
    return KnowledgeSourceRecord(
        id=identifier,
        title=f"Источник {identifier}",
        url=f"https://example.org/{identifier}",
        summary="Сводка источника.",
        keywords=["nutrients"],
        region="LV",
        languages=["ru"],
        plant_types=["all"],
        problem_types=["watering"],
        last_verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        next_review_at=datetime(2026, 12, 1, tzinfo=UTC),
        reviewed_by="reviewer",
        review_role="editorial",
        usage_basis="linked_factual_summary",
        source_version="2026-01",
    )


def test_exact_match_outranks_close_match_in_postgres_path(monkeypatch):
    exact = KnowledgeChunk(
        id=1, source_id="exact-source", source=_source_record("exact-source")
    )
    close = KnowledgeChunk(
        id=2, source_id="close-source", source=_source_record("close-source")
    )
    session = _FakeSession(chunks=[exact, close], distances=[0.0, 0.25])
    monkeypatch.setattr(
        "app.services.knowledge_service.create_embedding_provider", lambda: object()
    )
    monkeypatch.setattr(
        "app.services.knowledge_service._query_embedding",
        lambda provider, query: [1.0, 0.0],
    )
    results = _retrieve_from_database(session, "water", 4, None, "ru")
    assert [item.id for item in results] == ["exact-source", "close-source"]
