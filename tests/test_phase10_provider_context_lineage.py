from __future__ import annotations

from industrial_rag.retrieval_trace import RetrievalExecutionTrace


def test_provider_context_text_is_available_in_memory_but_not_persisted_payload() -> None:
    trace = RetrievalExecutionTrace(
        trace_version="test",
        original_query="q",
        normalized_query="q",
        retrieval_config=(),
        initial_results=(),
        rerank_applied=False,
        reranked_results=(),
        final_selected_chunks=(),
        selected_chunk_ids=(),
        normalization_ms=0.0,
        retrieval_ms=0.0,
        rerank_ms=0.0,
        evidence_selection_ms=0.0,
        provider_contexts=("actual provider context",),
    )

    assert trace.provider_contexts == ("actual provider context",)
    assert "provider_contexts" not in trace.to_payload()
