import json
from pathlib import Path

OUT = Path("evaluation/phase10b3i_r2")


def test_r2_restores_phase10b3d_policy_and_version():
    policy = json.loads((OUT / "metric_policy.json").read_text(encoding="utf-8"))
    assert policy["definition_version"] == "phase10b3d-metric-policy-v1"
    assert policy["restored_definition_version"] == "phase10b3d-metric-policy-v1"
    assert policy["source_path"] == "evaluation/phase10b3d/metric_policy.json"


def test_coverage_numerator_includes_overcitation():
    summary = json.loads((OUT / "coverage_funnel_summary.json").read_text(encoding="utf-8"))
    assert summary["coverage_numerator"] == summary["stage_counts"].get("covered_exact_citation", 0) + summary["stage_counts"].get("covered_with_overcitation", 0)
    assert summary["coverage_denominator"] == 39


def test_funnel_has_no_unknown_or_provider_answer_plan_fallback():
    rows = [json.loads(line) for line in (OUT / "coverage_funnel_matrix.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert all(row["final_failure_stage"] != "unknown_due_to_missing_audit_data" for row in rows)
    assert all("answer_plan" not in row["provider_evidence_ids_source"] for row in rows)


def test_citation_audit_exposes_precision_recall_and_overcitation():
    rows = [json.loads(line) for line in (OUT / "coverage_funnel_matrix.jsonl").read_text(encoding="utf-8").splitlines() if line]
    emitted = [row for row in rows if row["final_emitted"]]
    assert emitted
    assert all("citation_precision" in row["citation"] and "citation_recall" in row["citation"] for row in emitted)
    assert any(row["citation"]["classification"] == "supported_with_overcitation" for row in emitted)


def test_support_failure_fields_are_explicit_values_not_null():
    rows = [json.loads(line) for line in (OUT / "support_failure_cases.jsonl").read_text(encoding="utf-8").splitlines() if line]
    allowed = {True, False, "not_applicable", "ambiguous_needs_human_review"}
    for row in rows:
        for key in ("object_match", "parameter_match", "numeric_match", "unit_match", "condition_match", "model_match", "negation_match"):
            assert row[key] in allowed


def test_i1_dead_path_is_not_marked_eligible_without_coverage_lineage():
    rows = [json.loads(line) for line in (OUT / "i1_dead_path_matrix.jsonl").read_text(encoding="utf-8").splitlines() if line]
    summary = json.loads((OUT / "i1_dead_path_summary.json").read_text(encoding="utf-8"))
    assert len(rows) == 36
    assert summary["triggered"] == 0
    assert summary["trigger_eligible_count"] == 0
    assert all("missing_trace_field:coverage_before" in row["rejection_reason"] for row in rows)
