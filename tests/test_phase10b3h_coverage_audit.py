# ruff: noqa
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3h"
ALLOWED = {
    "retrieval_missing", "recalled_not_selected", "completion_not_triggered",
    "completion_rejected", "provider_context_missing", "generation_omitted",
    "generation_refusal", "grounding_false_negative", "grounding_false_positive",
    "citation_wrong_evidence", "evaluation_mapping_error", "unknown",
}


def test_coverage_funnel_schema_and_no_holdout():
    rows = [json.loads(x) for x in (OUT / "coverage_funnel_matrix.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert len(rows) == 72
    required = {"question_id", "expected_point_id", "expected_point_text_sha256", "expected_evidence_ids", "initial_recalled", "initial_best_rank", "selected", "completed", "available_to_provider", "generated", "grounding_retained", "final_emitted", "citation_correct", "final_failure_stage", "final_failure_reason"}
    assert required <= rows[0].keys()
    assert all(r["final_failure_stage"] in ALLOWED for r in rows)
    assert all(r["split"] in {"development", "validation"} for r in rows)


def test_disagreements_are_reviewed_three_cases():
    rows = [json.loads(x) for x in (OUT / "support_disagreement_cases.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert {r["question_id"] for r in rows} == {"S006", "S020", "A001"}
    allowed = {"mapping_correct_support_wrong", "numeric_mismatch", "unit_mismatch", "object_mismatch", "condition_mismatch", "page_or_chunk_mismatch", "lexical_false_positive", "golden_mapping_issue", "evaluator_issue"}
    assert all(set(r["classification"]) <= allowed for r in rows)
