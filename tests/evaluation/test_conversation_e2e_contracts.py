from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from evaluation.phase10.conversation_e2e_contracts import (
    JudgeConfig,
    assert_development_only,
    fingerprint_dataset,
    provider_context_payload,
    resolved_evaluation_user_input,
    runtime_config_fingerprint,
)

DATASET = Path("data/evaluation/conversation_retrieval_development.jsonl")


def test_fingerprint_preserves_frozen_development_dataset() -> None:
    fingerprint = fingerprint_dataset(DATASET)
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]

    assert fingerprint.case_count == 18
    assert list(fingerprint.case_ids) == [row["case_id"] for row in rows]
    assert len(fingerprint.raw_sha256) == 64
    assert len(fingerprint.semantic_sha256) == 64


def test_development_guard_rejects_validation_and_holdout_ids() -> None:
    with pytest.raises(ValueError, match="Development"):
        assert_development_only([{"source_question_id": "V001"}])
    with pytest.raises(ValueError, match="Development"):
        assert_development_only([{"source_question_id": "H001"}])


def test_evaluator_question_is_same_for_both_arms() -> None:
    case = {
        "dependent_query": "它的启动步骤呢？",
        "expected_standalone_query": "SUMMIT 泵的启动步骤是什么？",
    }
    assert resolved_evaluation_user_input(case) == case["expected_standalone_query"]


def test_provider_context_payload_uses_actual_trace_lineage() -> None:
    trace = SimpleNamespace(
        provider_context_order=("chunk-a", "chunk-b"),
        provider_evidence_ids=("E1", "E2"),
        provider_context_sha256="ctx-hash",
    )

    assert provider_context_payload(SimpleNamespace(retrieval_trace=trace)) == {
        "provider_evidence_ids": ["E1", "E2"],
        "provider_context_order": ["chunk-a", "chunk-b"],
        "provider_context_sha256": "ctx-hash",
    }


def test_runtime_fingerprint_excludes_query_text_and_is_equal_for_arms() -> None:
    settings = SimpleNamespace(
        qdrant_kb_id="kb",
        qdrant_generation="gen",
        working_dir=Path("workspace"),
        vector_backend=SimpleNamespace(value="qdrant"),
        embedding_model="text-embedding-v4",
        embedding_dim=1024,
        phase10b_query_mode="mix",
        phase10b_top_k=12,
        phase10b_chunk_top_k=20,
        query_normalization_enabled=True,
        answer_grounding_enabled=True,
        grounding_audit_enabled=True,
        evidence_selection_diversity_enabled=False,
        evidence_completion_enabled=True,
        evidence_completion_max=2,
        supplemental_retrieval_enabled=False,
        structured_citation_output_enabled=False,
        llm_base_url="https://example.invalid",
        llm_model="model-a",
        llm_fallback_models=("model-b",),
    )

    baseline = runtime_config_fingerprint(settings, query_text="dependent")
    candidate = runtime_config_fingerprint(settings, query_text="standalone")
    assert baseline.digest == candidate.digest
    assert "query_text" not in baseline.payload


def test_judge_config_is_serializable_and_explicit() -> None:
    config = JudgeConfig(
        ragas_version="0.3.9",
        faithfulness_metric="Faithfulness",
        response_relevancy_metric="ResponseRelevancy",
        judge_provider="dashscope",
        judge_model="qwen-plus",
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        temperature=0.0,
        seed=None,
        timeout_seconds=60,
        retry=2,
        max_concurrency=1,
    )

    payload = config.to_dict()
    assert payload["ragas_version"] == "0.3.9"
    assert payload["temperature"] == 0.0
    assert payload["max_concurrency"] == 1
