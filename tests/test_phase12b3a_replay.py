from __future__ import annotations

from industrial_rag.semantic_judge import SemanticSupport
from scripts.phase12b3a_run_replay import (
    classify_replay_status,
    project_semantic_citations,
    run_replay,
)


def test_projection_keeps_only_semantically_supported_runtime_evidence() -> None:
    response = {
        "claims": [
            {"claim_id": "c1", "text": "结论一"},
            {"claim_id": "c2", "text": "结论二"},
        ],
        "evidence": [
            {"evidence_id": "e1", "citation_id": "cite_1", "chunk_id": "chunk-1"},
            {"evidence_id": "e2", "citation_id": "cite_2", "chunk_id": "chunk-2"},
            {"evidence_id": "e3", "citation_id": "cite_3", "chunk_id": "chunk-3"},
        ],
    }
    judgements = {
        ("c1", "e1"): SemanticSupport.SUPPORTED,
        ("c1", "e2"): SemanticSupport.NOT_SUPPORTED,
        ("c1", "e3"): SemanticSupport.UNCERTAIN,
        ("c2", "e1"): SemanticSupport.SUPPORTED,
        ("c2", "e2"): SemanticSupport.PARTIALLY_SUPPORTED,
        ("c2", "e3"): SemanticSupport.NOT_SUPPORTED,
    }

    projected = project_semantic_citations(response, judgements)

    assert projected["selected_by_claim"] == {"c1": ("e1",), "c2": ("e1",)}
    assert [item["evidence_id"] for item in projected["citations"]] == ["e1"]


def test_projection_with_uncertain_matrix_does_not_fabricate_citation() -> None:
    response = {
        "claims": [{"claim_id": "c1", "text": "结论"}],
        "evidence": [{"evidence_id": "e1", "citation_id": "cite_1", "chunk_id": "chunk-1"}],
    }

    projected = project_semantic_citations(
        response,
        {("c1", "e1"): SemanticSupport.UNCERTAIN},
    )

    assert projected["citations"] == []
    assert projected["selected_by_claim"] == {}


def test_replay_does_not_call_judge_for_empty_runtime_matrix() -> None:
    calls: list[str] = []

    def fake_judge(prompt: str) -> str:
        calls.append(prompt)
        return '{"claims": []}'

    report = run_replay(judge=fake_judge)

    assert report["experiment_status"] == "blocked"
    assert calls == []


def test_invalid_judge_response_is_completed_but_not_blocked() -> None:
    assert classify_replay_status({"D001": "invalid_judge_response"}) == (
        "completed_with_invalid_judge_response"
    )


def test_missing_runtime_input_remains_blocked() -> None:
    assert classify_replay_status({"D001": "blocked_missing_runtime_evidence_text"}) == (
        "blocked"
    )
