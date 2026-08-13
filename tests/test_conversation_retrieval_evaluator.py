from __future__ import annotations

import asyncio

import pytest
from industrial_rag.lightrag_service import QueryOptions
from industrial_rag.query_normalization import normalize_query
from scripts.evaluate_conversation_retrieval_development import (
    DATASET_PATH,
    evaluate_backend,
    load_conversation_cases,
)


class RecordingBackend:
    def __init__(self, gold_by_query: dict[str, list[str]]) -> None:
        self.gold_by_query = gold_by_query
        self.calls: list[tuple[str, QueryOptions]] = []

    async def aquery_data(self, query: str, param: QueryOptions) -> dict[str, object]:
        self.calls.append((query, param))
        chunks = [
            {
                "content": (
                    f"[[INDUSTRIAL_RAG_SOURCE file=manual.pdf page=1 chunk={chunk_id}]]"
                ),
                "file_path": (
                    f"rag-source::manual.pdf::page=1::chunk={chunk_id}"
                ),
            }
            for chunk_id in self.gold_by_query.get(query, [])
        ]
        return {"status": "success", "data": {"chunks": chunks, "references": []}}


def _backend_for_cases(cases):
    gold_by_query = {}
    for case in cases:
        gold_by_query[normalize_query(case["expected_standalone_query"]).normalized_query] = list(
            reversed(case["gold_chunk_ids"])
        )
    return RecordingBackend(gold_by_query)


def test_evaluator_uses_two_calls_per_case_and_same_retrieval_options() -> None:
    cases = load_conversation_cases(DATASET_PATH)
    backend = _backend_for_cases(cases)
    config = QueryOptions(mode="mix", top_k=12, chunk_top_k=20, enable_rerank=False)

    report = asyncio.run(
        evaluate_backend(
            backend,
            cases=cases,
            config=config,
            fingerprint={
                "knowledge_base_id": "kb-development",
                "generation_id": "generation-development",
                "workspace": "workspace-development",
                "vector_backend": "qdrant",
                "embedding_model": "text-embedding-v4",
            },
        )
    )

    assert len(backend.calls) == 2 * len(cases)
    assert all(param == config for _, param in backend.calls)
    assert report["dataset"]["development_only_guard"] is True
    assert report["fingerprint"]["knowledge_base_id"] == "kb-development"
    assert report["status"] == "READY"


def test_evaluator_records_normalized_before_and_after_queries() -> None:
    cases = load_conversation_cases(DATASET_PATH)[:1]
    backend = _backend_for_cases(cases)
    report = asyncio.run(
        evaluate_backend(
            backend,
            cases=cases,
            config=QueryOptions(mode="mix", top_k=12, chunk_top_k=20),
            fingerprint={"knowledge_base_id": "kb", "generation_id": "g"},
        )
    )

    row = report["cases"][0]
    assert row["before_query"] == normalize_query(row["dependent_query"]).normalized_query
    assert row["after_query"] == normalize_query(row["rewritten_query"]).normalized_query
    assert row["rewrite_status"] == "rewritten"


def test_evaluator_fails_closed_when_rewrite_gold_does_not_match(monkeypatch) -> None:
    cases = load_conversation_cases(DATASET_PATH)[:1]
    backend = _backend_for_cases(cases)

    async def wrong_rewrite(self, query, history):
        from industrial_rag.conversation.query_rewriter import QueryRewriteResult

        return QueryRewriteResult(
            original_query=query,
            history_dependent=True,
            status="rewritten",
            rewrite_reason="pronoun_resolution",
            standalone_query="错误的独立问题",
            history_available=True,
            history_message_count=1,
            history_used=True,
        )

    monkeypatch.setattr(
        "scripts.evaluate_conversation_retrieval_development.QueryRewriter.rewrite",
        wrong_rewrite,
    )
    with pytest.raises(ValueError, match="rewrite gold mismatch"):
        asyncio.run(
            evaluate_backend(
                backend,
                cases=cases,
                config=QueryOptions(mode="mix", top_k=12, chunk_top_k=20),
                fingerprint={"knowledge_base_id": "kb", "generation_id": "g"},
            )
        )
