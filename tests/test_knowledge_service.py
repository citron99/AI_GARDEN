from datetime import date

import pytest

from app.services.knowledge_service import (
    KnowledgeSource,
    _query_embedding,
    _retrieve_static,
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
