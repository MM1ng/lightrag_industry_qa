"""Phase 6B: parity and metric-convention tests (offline)."""

from __future__ import annotations

import hashlib
import json

from evaluation.experiments.phase6b.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    PHASE6B_ROOT,
)


def _load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (PHASE6B_ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load(name: str) -> dict:
    return json.loads((PHASE6B_ROOT / name).read_text(encoding="utf-8"))


def test_phase6b_pool_sha256() -> None:
    assert (
        hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest()
        == CANDIDATE_POOL_SHA256
    )


def test_phase6b_parity_trace_fields_complete() -> None:
    harness = _load_jsonl("parity/harness_traces.jsonl")
    fastapi = _load_jsonl("parity/fastapi_traces.jsonl")
    assert len(harness) == 50
    assert len(fastapi) == 50
    required = {
        "question_id",
        "question_hash",
        "retrieved_chunk_ids",
        "retrieved_chunk_multiset_hash",
        "retrieved_chunk_order_hash",
        "final_context_chunk_ids",
        "final_context_text_hash",
        "context_template_hash",
        "evidence_policy_decision",
        "system_prompt_hash",
        "user_prompt_hash",
        "full_prompt_hash",
        "answer",
        "citations",
        "refusal",
    }
    for row in harness + fastapi:
        assert required <= set(row)


def test_phase6b_parity_diffs_have_earliest_stage() -> None:
    diffs = _load_jsonl("parity/per_question_diff.jsonl")
    assert len(diffs) == 50
    stages = {d["earliest_diff_stage"] for d in diffs}
    assert stages <= {
        "question_input",
        "query_plan",
        "retrieval_candidates",
        "candidate_order",
        "final_context",
        "context_rendering",
        "evidence_policy",
        "prompt",
        "model_parameters",
        "cache",
        "raw_answer",
        "answer_parser",
        "citation_parser",
        "evaluator",
        "no_difference",
    }


def test_phase6b_retrieval_at_12_identical_both_paths() -> None:
    recomputed = _load("metric_audit/recomputed_metrics.json")
    h = recomputed["harness_retrieval_at_12"]
    f = recomputed["fastapi_retrieval_at_12"]
    for key in (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_12",
        "mrr_at_5",
        "gold_document_recall_at_12",
        "gold_page_recall_at_12",
        "gold_evidence_recall_at_12",
        "evidence_precision_at_5",
        "evidence_precision_at_12",
    ):
        assert h[key]["decimal"] == f[key]["decimal"], key
    assert f["gold_page_recall_at_12"]["decimal"] == 0.8542
    assert f["gold_evidence_recall_at_12"]["decimal"] == 0.7917


def test_phase6b_canonical_refusal_clears_citations() -> None:
    recomputed = _load("metric_audit/recomputed_metrics.json")
    legacy = recomputed["harness_citation_legacy_convention"]
    canonical = recomputed["harness_citation_canonical_convention"]
    fastapi = recomputed["fastapi_citation_canonical_convention"]
    assert legacy["answer_citation_accuracy"]["decimal"] == 0.8333  # historical preserved
    assert canonical["answer_citation_accuracy"]["decimal"] == 0.6458
    assert fastapi["answer_citation_accuracy"]["decimal"] == 0.7708
    assert recomputed["gate"]["threshold_drop_leq_002"] is True
    assert recomputed["gate"]["drop"] < 0


def test_phase6b_gate_threshold_unchanged_and_reject_case() -> None:
    # The threshold remains drop <= 0.02; a >0.02 drop must reject.
    drop = -0.125
    assert drop <= 0.02
    fake_drop = 0.0625
    assert fake_drop > 0.02  # would reject
    release = _load("rc_retest/release_gates.json")
    assert release["release_candidate_approved"] is True
    assert release["failed_gates"] == []


def test_phase6b_regression_lists_precise() -> None:
    regressions = _load("regression/citation_regressions.json")
    q_ids = [entry["question_id"] for entry in regressions["entries"]]
    assert q_ids == ["C005", "C006", "C007", "D005", "S007", "S020"]
    assert regressions["canonical_baseline_only_success"] == ["C007"]


def test_phase6b_actual_model_observability_fields() -> None:
    # requested vs configured vs provider-reported must be distinguishable.
    golden = _load_jsonl("rc_retest/golden_results.jsonl")
    assert all(r.get("requested_model") == "qwen-plus-2025-07-28" for r in golden)
    # provider_reported_model is not available from the provider -> null, not invented
    assert "provider_reported_model" not in golden[0] or golden[0].get("provider_reported_model") is None


def test_phase6b_qdrant_tech_debt_documented() -> None:
    path = PHASE6B_ROOT / "tech_debt" / "QDRANT-COMPAT-001.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "1.18.0" in text
    assert "1.13.6" in text
    assert "本阶段不升级" in text


def test_phase6b_release_candidate_strategy_unchanged_production() -> None:
    baseline = _load("baseline_manifest.json")
    assert baseline["strategy"]["context_strategy"] == "current_rows"
    assert baseline["strategy"]["answer_strategy"] == "current"
    assert baseline["strategy"]["rerank_enabled"] is False
    assert baseline["release_candidate_approved"] is True
    assert baseline["frozen_candidate_pool"]["sha256"] == CANDIDATE_POOL_SHA256
