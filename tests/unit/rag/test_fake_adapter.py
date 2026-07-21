from __future__ import annotations

import pytest

from industrial_energy_agent.rag.base import RAGCapabilityError, RAGDocument
from industrial_energy_agent.rag.fake_adapter import FakeRAGAdapter


def test_fake_search_returns_deterministic_empty_evidence() -> None:
    adapter = FakeRAGAdapter()

    first = adapter.search("轴承温度异常", mode="hybrid", top_k=5)
    second = adapter.search("轴承温度异常", mode="hybrid", top_k=5)

    assert first == second
    assert first.mode == "hybrid"
    assert first.chunks == ()
    assert adapter.call_summaries[-1].operation == "search"


def test_fake_ingest_does_not_retain_document_text() -> None:
    sensitive_body = "test-only-sensitive-document-body"
    adapter = FakeRAGAdapter()

    result = adapter.ingest_documents(
        [RAGDocument(text=sensitive_body, file_source="manual-marker.txt")]
    )

    assert result.track_id == "fake-insert-0001"
    assert sensitive_body not in repr(adapter)
    assert all(sensitive_body not in repr(summary) for summary in adapter.call_summaries)


def test_fake_rejects_unverified_mode() -> None:
    adapter = FakeRAGAdapter()

    with pytest.raises(RAGCapabilityError):
        adapter.search("轴承温度异常", mode="bypass", top_k=5)  # type: ignore[arg-type]
