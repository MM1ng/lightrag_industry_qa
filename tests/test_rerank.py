"""Phase 4D: rerank interface/gate tests (offline, no external calls)."""

from __future__ import annotations

import asyncio
import json

import pytest
from evaluation.experiments.phase4.rerank.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    EXPERIMENT_ROOT,
    RERANK_CONFIG,
)
from evaluation.experiments.phase4.rerank.reranker import (
    BlockedReranker,
    RerankConfigurationError,
    RerankedCandidate,
    cache_key,
    rerank_gate,
    resolve_rerank_model,
)


def _candidates(n: int = 20) -> list[dict]:
    return [
        {
            "chunk_id": f"c{i}",
            "child_text_hash": f"h{i}",
            "document_id": "a.pdf",
            "page": 1 + i,
            "original_rank": i + 1,
            "original_score": 0.5 - i * 0.01,
        }
        for i in range(n)
    ]


def test_candidate_pool_manifest_matches_frozen_results() -> None:
    manifest_path = EXPERIMENT_ROOT / "manifests" / "candidate_pool_manifest.json"
    if not manifest_path.is_file():
        pytest.skip("candidate pool manifest absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["hash_matches"] is True
    assert manifest["expected_sha256"] == CANDIDATE_POOL_SHA256
    assert manifest["question_count"] == 50
    assert manifest["candidate_k"] == 20
    assert manifest["final_k"] == 12
    assert manifest["parser_pipeline"] == "pymupdf_standard_adapter"
    assert manifest["candidate_count_per_question"]["S001"] == 20
    assert manifest["chunk_hash_summary"]["unique"] <= manifest["chunk_hash_summary"]["count"]


def test_candidate_pool_sha256() -> None:
    import hashlib

    assert hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest() == CANDIDATE_POOL_SHA256


def test_rerank_config_defaults_off() -> None:
    assert RERANK_CONFIG["rerank_enabled"] is False
    assert RERANK_CONFIG["rerank_fallback_enabled"] is False
    assert RERANK_CONFIG["candidate_k"] == 20
    assert RERANK_CONFIG["final_k"] == 12
    assert RERANK_CONFIG["parent_expansion"] == "none"


def test_exact_model_required_and_latest_rejected() -> None:
    assert resolve_rerank_model({"RERANK_MODEL": "bge-reranker-v2-m3"}) == "bge-reranker-v2-m3"
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "latest"})
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "model-latest"})
    assert resolve_rerank_model({"RERANK_MODEL": ""}) is None


def test_gate_blocks_when_model_missing() -> None:
    result = rerank_gate({"RERANK_MODEL": "", "RERANK_FALLBACK_ENABLED": "false"})
    assert result["allowed"] is False
    assert result["model"] is None


def test_gate_blocks_when_fallback_enabled() -> None:
    result = rerank_gate({"RERANK_MODEL": "exact-model", "RERANK_FALLBACK_ENABLED": "true"})
    assert result["allowed"] is False


def test_gate_allows_exact_model_with_fallback_off() -> None:
    result = rerank_gate({"RERANK_MODEL": "exact-model", "RERANK_FALLBACK_ENABLED": "false"})
    assert result["allowed"] is True
    assert result["model"] == "exact-model"


def test_blocked_reranker_raises() -> None:
    reranker = BlockedReranker()

    async def call():
        return await reranker.rerank("q", _candidates(), 12)

    with pytest.raises(RerankConfigurationError):
        asyncio.run(call())


def test_cache_key_is_exact_and_deterministic() -> None:
    a = cache_key("q", _candidates(), "model-x")
    b = cache_key("q", _candidates(), "model-x")
    c = cache_key("q", _candidates(), "model-y")
    d = cache_key("q", _candidates()[:10], "model-x")
    assert a == b
    assert a != c
    assert a != d


def test_reranked_candidate_preserves_original_fields() -> None:
    cand = RerankedCandidate(
        chunk_id="c1",
        original_rank=5,
        original_score=0.4,
        rerank_rank=1,
        rerank_score=0.9,
        document_id="a.pdf",
        page=6,
        text_hash="h",
        model="m",
        latency=0.1,
        status="ok",
    ).to_dict()
    assert cand["original_rank"] == 5
    assert cand["original_score"] == 0.4
    assert cand["rerank_rank"] == 1
    assert cand["rerank_score"] == 0.9


def test_r0_baseline_metrics_use_canonical_mrr() -> None:
    metrics_path = EXPERIMENT_ROOT / "results" / "offline" / "baseline_metrics.json"
    if not metrics_path.is_file():
        pytest.skip("R0 baseline metrics absent")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["evidence_questions"] == 48
    assert metrics["recall_at_1"] <= metrics["recall_at_3"] <= metrics["recall_at_5"] <= metrics["recall_at_12"]
    assert 0 < metrics["mrr"] <= 1
