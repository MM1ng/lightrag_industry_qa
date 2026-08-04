import json
from pathlib import Path

from scripts.phase10b3i_r1_repair import classify


def _row(*, selected=True, available=True, retained=True, citation_chunk="c1"):
    selected_chunks = [{"chunk_id": "c1"}] if selected else []
    evidence = [{"evidence_id": "E1", "chunk_id": "c1", "generation_id": "g1"}] if available else []
    claims = [{"claim_id": "P1", "text": "answer", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]}] if retained else []
    return {
        "question_id": "S001",
        "split": "development",
        "golden": {"question_id": "S001", "expected_evidence": [{"evidence_id": "S001-e1", "chunk_id": "c1"}], "expected_answer_points": [{"point_id": "S001-p1", "supported_by": ["S001-e1"]}], "answerable": True},
        "response": {"status": "success", "claims": claims, "citations": [{"citation_id": "cite_1", "chunk_id": citation_chunk, "generation_id": "g1", "evidence_id": "E1"}] if available else [], "evidence": evidence},
        "trace": {"generation_id": "g1", "initial_results": [{"chunk_id": "c1"}], "final_selected_chunks": selected_chunks, "completed_evidence": [], "answer_plan": [{"point_id": "P1", "evidence_ids": ["E1"]}], "grounding_audit": {"point_decisions": [{"point_id": "P1"}], "retained_answer_points": [{"point_id": "P1"}] if retained else []}},
    }


def test_success_point_enters_covered_final_emitted():
    row = _row()
    result = classify(row, row["golden"]["expected_answer_points"][0], {})
    assert result["final_failure_stage"] == "covered_final_emitted"
    assert result["citation_correct"] is True


def test_available_provider_cannot_be_selected_not_available():
    row = _row(selected=True, available=True)
    result = classify(row, row["golden"]["expected_answer_points"][0], {})
    assert result["available_to_provider"] is True
    assert result["final_failure_stage"] != "selected_not_available_to_provider"


def test_citation_false_only_for_final_emitted_and_non_emitted_is_null():
    row = _row(citation_chunk="wrong")
    point = row["golden"]["expected_answer_points"][0]
    result = classify(row, point, {})
    assert result["final_emitted"] is True
    assert result["citation_correct"] is False
    row = _row(retained=False)
    result = classify(row, point, {})
    assert result["final_emitted"] is False
    assert result["citation_correct"] is None


def test_dynamic_expected_point_count_and_no_holdout_artifacts():
    out = Path("evaluation/phase10b3i_r1")
    invariants = json.loads((out / "coverage_funnel_invariants.json").read_text(encoding="utf-8"))
    assert invariants["development_expected_point_count"] == 39
    assert invariants["final_funnel_valid"] is True
    assert invariants["no_validation"] is True
    assert invariants["no_holdout"] is True


def test_funnel_categories_are_mutually_exclusive_and_counts_close():
    summary = json.loads(Path("evaluation/phase10b3i_r1/coverage_funnel_summary.json").read_text(encoding="utf-8"))
    assert sum(summary["stage_counts"].values()) == summary["point_count"]
    assert summary["unknown_count"] == 0


def test_support_file_is_not_full_funnel_copy_and_citation_count_matches_metric():
    out = Path("evaluation/phase10b3i_r1")
    support = (out / "support_failure_cases.jsonl").read_text(encoding="utf-8")
    funnel = (out / "coverage_funnel_matrix.jsonl").read_text(encoding="utf-8")
    assert support != funnel
    metrics = json.loads((out / "i0_development_metrics.json").read_text(encoding="utf-8"))["metrics"]["question_level_citation_accuracy"]
    citation_summary = json.loads((out / "citation_failure_summary.json").read_text(encoding="utf-8"))
    assert citation_summary["question_count"] == metrics["denominator"] - metrics["numerator"]
