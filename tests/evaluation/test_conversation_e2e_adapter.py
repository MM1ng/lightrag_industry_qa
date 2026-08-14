from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from evaluation.phase10.conversation_e2e_adapter import run_case
from industrial_rag.citation_formatter import Citation
from industrial_rag.conversation.query_rewriter import QueryRewriteResult
from industrial_rag.query_normalization import normalize_query


@dataclass
class RecordingService:
    calls: list[tuple[str, dict[str, object]]]

    async def query(self, question: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((question, kwargs))
        trace = SimpleNamespace(
            normalized_query=question,
            selected_chunk_ids=("gold",),
            initial_results=(),
            final_selected_chunks=(),
            provider_evidence_ids=("E1",),
            provider_primary_evidence_ids=("E1",),
            provider_completed_evidence_ids=(),
            provider_supplemental_evidence_ids=(),
            provider_context_order=("gold",),
            provider_context_sha256="context-hash",
            rewrite_status="unchanged",
            rewrite_reason="none",
            history_used=False,
            grounding_audit=None,
        )
        return SimpleNamespace(
            answer="依据答案",
            answer_status="success",
            citations=(Citation("manual.pdf", 1, "gold"),),
            answer_points=(),
            retrieval_chunk_ids=("gold",),
            retrieval_meta=(),
            grounding_failure_categories=(),
            retrieval_trace=trace,
        )


class RecordingRewriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def rewrite(self, question: str, history: list[dict[str, str]]) -> QueryRewriteResult:
        self.calls.append((question, history))
        return QueryRewriteResult(
            original_query=question,
            standalone_query="SUMMIT 泵的启动步骤是什么？",
            status="rewritten",
            rewrite_reason="resolved_reference",
            history_available=True,
            history_message_count=len(history),
            history_used=True,
            history_dependent=True,
        )


@pytest.mark.asyncio
async def test_baseline_bypasses_rewriter_and_calls_light_rag_with_dependent_query() -> None:
    service = RecordingService([])
    rewriter = RecordingRewriter()
    case = {
        "case_id": "conv-s001",
        "dependent_query": "它的启动步骤呢？",
        "history": [{"role": "user", "content": "SUMMIT 泵"}],
        "expected_standalone_query": "SUMMIT 泵的启动步骤是什么？",
        "gold_chunk_ids": ["gold"],
    }

    result = await run_case(service, case, mode="mix", top_k=12, chunk_top_k=20, rewriter=rewriter)

    assert service.calls[0][0] == case["dependent_query"]
    assert service.calls[0][0] == case["dependent_query"]
    assert rewriter.calls == [(case["dependent_query"], case["history"])]
    assert result["baseline"]["runtime_query"] == case["dependent_query"]
    assert result["baseline"]["provider_context_hash"] == "context-hash"
    assert result["gold_chunk_ids"] == ["gold"]
    assert result["baseline"]["citations"] == [
        {"source_file": "manual.pdf", "page_number": 1, "chunk_id": "gold"}
    ]
    json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_candidate_uses_frozen_rewriter_and_same_downstream_options() -> None:
    service = RecordingService([])
    rewriter = RecordingRewriter()
    case = {
        "case_id": "conv-s001",
        "dependent_query": "它的启动步骤呢？",
        "history": [{"role": "user", "content": "SUMMIT 泵"}],
        "expected_standalone_query": "SUMMIT 泵的启动步骤是什么？",
        "gold_chunk_ids": ["gold"],
    }

    result = await run_case(service, case, mode="mix", top_k=12, chunk_top_k=20, rewriter=rewriter)

    assert service.calls[1][0] == normalize_query(case["expected_standalone_query"]).normalized_query
    assert service.calls[0][1] == service.calls[1][1]
    assert rewriter.calls == [(case["dependent_query"], case["history"])]
    assert result["candidate"]["rewrite_status"] == "rewritten"
    assert result["candidate"]["evaluation_user_input"] == case["expected_standalone_query"]
