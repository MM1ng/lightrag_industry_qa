"""Integrity checks for the saved Phase 10B-3D metric artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = ROOT / "evaluation" / "phase10b3d"


def _metrics() -> dict:
    return json.loads((PHASE / "recomputed_baseline_metrics.json").read_text(encoding="utf-8"))


def test_partial_answers_are_in_substantive_denominators() -> None:
    metrics = _metrics()
    assert metrics["status_counts"]["positive"]["partial_answer"] == 39
    assert metrics["question_level_unsupported_answer_rate"]["denominator"] == 40
    assert metrics["question_level_citation_accuracy"]["denominator"] == 40


def test_claim_mapping_uses_emitted_claim_denominator() -> None:
    metrics = _metrics()
    mapping = metrics["claim_citation_exact_mapping_rate"]
    assert mapping["numerator"] == 271
    assert mapping["denominator"] == 271


def test_metric_invariants_gate_is_explicit() -> None:
    payload = json.loads((PHASE / "metric_invariant_check.json").read_text(encoding="utf-8"))
    assert payload["final_metrics_valid"] is True
    assert all(payload["checks"].values())


def test_missing_expected_points_are_not_unsupported_emitted_points() -> None:
    metrics = _metrics()
    assert metrics["missing_expected_answer_point_rate"]["numerator"] == 30
    assert metrics["unsupported_emitted_answer_point_rate"]["numerator"] == 0


def test_table_unsupported_is_null() -> None:
    table = _metrics()["table_trigger_rate"]
    assert table == {
        "supported": False,
        "numerator": None,
        "denominator": None,
        "value": None,
        "reason": "no reliable table metadata in candidate artifacts",
    }


def test_phase10_evaluator_counts_partial_as_substantive() -> None:
    from industrial_rag.phase10_evaluation import evaluate_retrieval

    expected = {"document_name": "manual.pdf", "page_number": 1, "chunk_id": "c1", "relevance_grade": 2}
    case = {
        "golden": {"answerable": True, "expected_evidence": [expected]},
        "response": {"status": "partial_answer", "citations": [{"chunk_id": "c1"}]},
        "trace": {"initial_results": [{"initial_rank": 1, "chunk_id": "c1", "document_name": "manual.pdf", "page_number": 1}], "retrieval_ms": 1, "rerank_ms": 0},
    }
    metrics = evaluate_retrieval([case])
    assert metrics["overall"]["unsupported_answer_rate"]["denominator"] == 1
    assert metrics["overall"]["question_level_citation_accuracy"]["denominator"] == 1
