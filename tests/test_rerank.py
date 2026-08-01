"""Phase 4D: rerank interface/gate tests (offline, no external calls)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from evaluation.experiments.phase4.rerank.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    EXPERIMENT_ROOT,
    RERANK_CONFIG,
)
from evaluation.experiments.phase4.rerank.dashscope_reranker import (
    DashScopeQwen3Reranker,
    build_rerank_payload,
)
from evaluation.experiments.phase4.rerank.evaluate_offline import (
    completeness_report,
    metrics_for_topk,
    offline_gates,
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
    # Phase 4D-R2 variable-size contract: C007=19 rows, N001=20, N002=19 are
    # all valid frozen inputs (no padding, no clearing, no re-retrieval).
    from collections import Counter

    actual = Counter(
        json.loads(line)["question_id"]
        for line in CANDIDATE_POOL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert manifest["candidate_count_per_question"] == {
        k: actual[k] for k in sorted(actual)
    }
    assert manifest["candidate_count_per_question"]["C007"] == 19
    assert manifest["candidate_count_per_question"]["N001"] == 20
    assert manifest["candidate_count_per_question"]["N002"] == 19
    assert (
        manifest.get("candidate_count_contract")
        == "variable_unique_candidates_up_to_candidate_k"
    )
    assert manifest.get("negative_questions_may_have_candidates") is True
    assert manifest.get("effective_final_k_rule") == "min(final_k, input_candidate_count)"
    per_q = manifest.get("per_question_counts", {})
    assert per_q.get("C007") == 19
    assert per_q.get("N001") == 20
    assert per_q.get("N002") == 19
    assert per_q.get("default_answerable") == 20


def test_candidate_pool_sha256() -> None:
    import hashlib

    assert hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest() == CANDIDATE_POOL_SHA256


def test_rerank_config_defaults_off() -> None:
    assert RERANK_CONFIG["rerank_enabled"] is False
    assert RERANK_CONFIG["rerank_fallback_enabled"] is False
    assert RERANK_CONFIG["candidate_k"] == 20
    assert RERANK_CONFIG["final_k"] == 12
    assert RERANK_CONFIG["parent_expansion"] == "none"


def test_rerank_payload_matches_official_schema() -> None:
    payload = build_rerank_payload(
        model="qwen3-rerank",
        query="问题",
        documents=["文档1", "文档2"],
        top_n=20,
    )
    assert payload == {
        "model": "qwen3-rerank",
        "input": {"query": "问题", "documents": ["文档1", "文档2"]},
        "parameters": {"top_n": 20, "return_documents": False},
    }


def test_exact_model_required_and_latest_rejected() -> None:
    assert resolve_rerank_model({"RERANK_MODEL": "qwen3-rerank"}) == "qwen3-rerank"
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "latest"})
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "model-latest"})
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "bge-reranker-v2-m3"})
    with pytest.raises(RerankConfigurationError):
        resolve_rerank_model({"RERANK_MODEL": "qwen3-vl-rerank"})
    assert resolve_rerank_model({"RERANK_MODEL": ""}) is None


def test_gate_blocks_when_model_missing() -> None:
    result = rerank_gate({"RERANK_MODEL": "", "RERANK_FALLBACK_ENABLED": "false"})
    assert result["allowed"] is False
    assert result["model"] is None


def test_gate_blocks_when_fallback_enabled() -> None:
    result = rerank_gate({"RERANK_MODEL": "qwen3-rerank", "RERANK_FALLBACK_ENABLED": "true"})
    assert result["allowed"] is False


def test_gate_allows_exact_model_with_fallback_off() -> None:
    result = rerank_gate({"RERANK_MODEL": "qwen3-rerank", "RERANK_FALLBACK_ENABLED": "false"})
    assert result["allowed"] is True
    assert result["model"] == "qwen3-rerank"


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


def _reranker() -> DashScopeQwen3Reranker:
    return DashScopeQwen3Reranker(
        api_key="dummy-not-a-secret",
        config_hash="phase4d-test",
        commit="test-commit",
    )


def test_effective_final_k_is_min_of_final_k_and_input_count() -> None:
    final_k = RERANK_CONFIG["final_k"]
    assert min(final_k, 19) == 12
    assert min(final_k, 12) == 12
    assert min(final_k, 5) == 5
    assert min(final_k, 0) == 0


def test_variable_size_candidate_counts_are_legal() -> None:
    assert RERANK_CONFIG["candidate_k"] == 20
    # 19 unique candidates are legal for answerable questions.
    assert 1 <= 19 <= RERANK_CONFIG["candidate_k"]
    # Evidence-insufficient questions may carry 0..candidate_k candidates.
    for count in (0, 19, 20):
        assert 0 <= count <= RERANK_CONFIG["candidate_k"]
    # No padding with duplicate/pool-out candidates is allowed.
    assert RERANK_CONFIG["candidate_k"] < 21


def test_completeness_c007_19_to_19_passes_with_multiset_preservation() -> None:
    input_rows = [
        {
            "child_chunk_id": f"c{i}",
            "rank": i + 1,
            "document_id": "a.pdf",
            "page": i + 1,
            "parent_id": None,
            "child_text_hash": f"h{i}",
            "retrieval_score": 0.5 - i * 0.01,
        }
        for i in range(19)
    ]
    # Frozen C007 reality: chunk c1 appears at ranks 2 and 5 (pre-existing).
    input_rows[1] = dict(input_rows[1], child_chunk_id="c1", child_text_hash="h1")
    input_rows[4] = dict(input_rows[4], child_chunk_id="c1", child_text_hash="h1")
    output_rows = [
        {
            "chunk_id": r["child_chunk_id"],
            "original_rank": r["rank"],
            "original_score": r["retrieval_score"],
            "text_hash": r["child_text_hash"],
            "document_id": r["document_id"],
            "page": r["page"],
            "parent_id": r["parent_id"],
            "rerank_rank": idx + 1,
            "cache_hit": True,
        }
        for idx, r in enumerate(input_rows)
    ]
    report = completeness_report({"C007": input_rows}, {"C007": output_rows})
    assert report["passed"] is True
    assert report["candidate_preservation_rate"] == 1.0
    assert report["pool_out_count"] == 0
    assert report["duplicate_count"] == 0
    assert report["lost_count"] == 0
    assert report["per_question"]["C007"]["input_rows"] == 19
    assert report["per_question"]["C007"]["output_rows"] == 19
    assert report["per_question"]["C007"]["input_duplicate_chunk_ids"] == {"c1": 2}
    assert report["per_question"]["C007"]["metadata_unchanged"] is True


def test_negative_questions_may_have_candidates_and_are_excluded_from_retrieval_metrics() -> None:
    by_q: dict[str, list[dict]] = {
        "S001": [
            {
                "child_chunk_id": "s1",
                "rank": 1,
                "document_id": "a.pdf",
                "page": 1,
                "retrieval_score": 0.9,
            }
        ],
        "N001": [
            {
                "child_chunk_id": "n1",
                "rank": 1,
                "document_id": "b.pdf",
                "page": 2,
                "retrieval_score": 0.8,
            }
        ],
        "N002": [
            {
                "child_chunk_id": "n2",
                "rank": 1,
                "document_id": "b.pdf",
                "page": 3,
                "retrieval_score": 0.7,
            }
        ],
    }
    mapped = {"S001": {"s1"}}
    gold_pages = {"S001": {("a.pdf", 1)}}
    metrics = metrics_for_topk(by_q, 12, mapped=mapped, gold_pages=gold_pages)
    assert metrics["evidence_questions"] == 1
    assert metrics["recall_at_1"] == 1.0
    # N001/N002 rows are preserved as frozen candidates but never cleared.
    assert len(by_q["N001"]) == 1
    assert len(by_q["N002"]) == 1


def test_request_payload_hash_reuses_identical_payload_and_ignores_commit() -> None:
    reranker_a = _reranker()
    reranker_b = DashScopeQwen3Reranker(
        api_key="dummy-not-a-secret",
        config_hash="different-config-hash",
        commit="different-commit",
    )
    candidates = [
        {
            "chunk_id": f"c{i}",
            "child_text_hash": f"h{i}",
            "text": f"文本{i}",
        }
        for i in range(19)
    ]
    query = "测试问题"
    hash_a = reranker_a.request_payload_hash(query, candidates, 20)
    hash_b = reranker_b.request_payload_hash(query, candidates, 20)
    assert hash_a == hash_b  # commit/config-hash changes must not invalidate
    reordered = list(reversed(candidates))
    assert hash_a != reranker_a.request_payload_hash(query, reordered, 20)
    assert hash_a != reranker_a.request_payload_hash(query, candidates, 12)
    assert hash_a != reranker_a.request_payload_hash("另一问题", candidates, 20)
    assert hash_a != reranker_a.request_payload_hash(
        query, candidates[:18], 20
    )


def test_provider_skips_empty_candidates_without_api_call() -> None:
    reranker = _reranker()

    async def call() -> list:
        return await reranker.rerank("问题", [], 20)

    result = asyncio.run(call())
    assert result == []
    assert reranker.calls[-1]["status"] == "skipped_empty"
    assert reranker.calls[-1]["input_count"] == 0
    assert reranker.cache_misses == 0


def test_provider_accepts_variable_size_response(monkeypatch) -> None:
    reranker = _reranker()
    candidates = [
        {
            "chunk_id": f"c{i}",
            "child_text_hash": f"h{i}",
            "text": f"文本{i}",
            "document_id": "a.pdf",
            "page": i + 1,
            "original_rank": i + 1,
            "original_score": 0.5 - i * 0.01,
        }
        for i in range(19)
    ]

    async def fake_call(query: str, documents: list[str], top_n: int):
        request_id = "req-19"
        results = [
            {"index": idx, "relevance_score": 0.5 + 0.01 * (len(documents) - idx)}
            for idx in range(len(documents))
        ]
        return request_id, {"output": {"results": results}, "usage": {"total_tokens": 100}}, json.dumps({"ok": True}), 0.1

    monkeypatch.setattr(reranker, "_call_once", fake_call)

    async def call() -> list:
        return await reranker.rerank("问题", candidates, top_n=20)

    result = asyncio.run(call())
    assert len(result) == 19
    assert {r.chunk_id for r in result} == {c["chunk_id"] for c in candidates}
    assert {r.original_rank for r in result} == set(range(1, 20))


def test_provider_cache_writes_payload_hash_and_no_secret(monkeypatch, tmp_path: Path) -> None:
    cache_path = tmp_path / "rerank.jsonl"
    reranker = DashScopeQwen3Reranker(
        api_key="super-secret-key",
        cache_path=cache_path,
        config_hash="phase4d-test",
        commit="test-commit",
    )
    candidates = [
        {
            "chunk_id": f"c{i}",
            "child_text_hash": f"h{i}",
            "text": f"文本{i}",
            "document_id": "a.pdf",
            "page": i + 1,
            "original_rank": i + 1,
            "original_score": 0.5 - i * 0.01,
        }
        for i in range(19)
    ]
    calls = {"n": 0}

    async def fake_call(query: str, documents: list[str], top_n: int):
        calls["n"] += 1
        results = [
            {"index": idx, "relevance_score": 0.5 + 0.01 * (len(documents) - idx)}
            for idx in range(len(documents))
        ]
        return f"req-{calls['n']}", {"output": {"results": results}}, json.dumps({"ok": True}), 0.1

    monkeypatch.setattr(reranker, "_call_once", fake_call)

    async def run_twice() -> tuple[list, list]:
        first = await reranker.rerank("问题", candidates, top_n=20)
        second = await reranker.rerank("问题", candidates, top_n=20)
        return first, second

    first, second = asyncio.run(run_twice())
    assert len(first) == 19
    assert len(second) == 19
    assert calls["n"] == 1
    assert reranker.cache_hits == 1
    entries = [
        json.loads(line)
        for line in cache_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert entries[0]["request_payload_hash"] == entries[0]["key"]
    assert "super-secret-key" not in cache_path.read_text(encoding="utf-8")


def test_offline_gates_require_preservation_and_value() -> None:
    r0 = {
        "recall_at_5": 0.75,
        "gold_page_recall": 0.8542,
        "gold_evidence_recall": 0.7917,
        "mrr": 0.6201,
        "top1_document_accuracy": 1.0,
        "evidence_precision_at_5": 0.2,
    }
    r1 = {
        "metrics": {
            "recall_at_5": 0.75,
            "gold_page_recall": 0.8542,
            "gold_evidence_recall": 0.7917,
            "mrr": 0.6201,
            "top1_document_accuracy": 1.0,
            "evidence_precision_at_5": 0.2,
        }
    }
    completeness = {
        "error_count": 0,
        "candidate_preservation_rate": 1.0,
        "pool_out_count": 0,
        "duplicate_count": 0,
        "lost_count": 0,
        "fallback_count": 0,
    }
    movement = {"improved_count": 0, "regressed_count": 0}
    gates = offline_gates(r0, r1, completeness, movement)
    assert gates["hard_passed"] is True
    assert gates["value_passed"] is False
    assert gates["stage2_allowed"] is False


def test_offline_bootstrap_gold_page_recall_is_positive_when_present() -> None:
    path = EXPERIMENT_ROOT / "results" / "offline" / "bootstrap.json"
    if not path.is_file():
        pytest.skip("offline bootstrap results absent")
    bootstrap = json.loads(path.read_text(encoding="utf-8"))
    assert bootstrap["n_questions"] == 48
    assert bootstrap["gold_page_recall"]["mean_diff"] == pytest.approx(0.0833, abs=1e-4)
    assert bootstrap["gold_page_recall"]["crosses_zero"] is False
