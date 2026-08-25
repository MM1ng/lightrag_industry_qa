"""Offline contracts for Phase 10B-3J final J0 certification artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3j_goal"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_goal_certification_outputs_are_truthful_and_offline() -> None:
    from scripts.phase10b3j_goal_certification import main

    assert main() == 0
    metrics = json.loads((OUT / "j0_development_metrics.json").read_text(encoding="utf-8"))
    assert metrics["metric_definition"]["definition_version"] == "phase10b3d-metric-policy-v1"
    assert metrics["input_evidence"]["golden_set_read"] is False
    assert metrics["input_evidence"]["development_golden_sidecar_read"] is True
    assert metrics["input_evidence"]["holdout_read"] is False
    assert metrics["input_evidence"]["model_queries_made"] is False
    assert metrics["quality_point_record_count"] == 39
    matrix_inputs = metrics["quality_metrics"]["j0_matrix_inputs"]
    assert matrix_inputs["grounding_retained_matrix_matches_trace"] is False
    assert matrix_inputs["grounding_retained_matrix_mismatch_question_ids"]
    comparison = metrics["r2_non_regression_gates"]["quality_metric_comparison"]
    assert set(comparison) == set(metrics["quality_metrics"]["metrics"])
    assert all(item["j0_value"] is not None for item in comparison.values())
    assert metrics["r2_non_regression_gates"]["passed"] is True


def test_lifecycle_fixture_keeps_active_pointer_and_blocks_terminal_states() -> None:
    lifecycle = json.loads((OUT / "lifecycle_contract_results.json").read_text(encoding="utf-8"))
    assert lifecycle["fixture"]["candidate_database_opened"] is False
    assert lifecycle["normal_queries_keep_active"] is True
    assert lifecycle["active_pointer_unchanged"] is True
    assert lifecycle["contracts"]["normal_query"]["http_status"] == 200
    assert lifecycle["contracts"]["ready"]["http_status"] == 200
    assert lifecycle["contracts"]["building"] == {"http_status": 409, "code": "generation_invalid_state"}
    assert lifecycle["contracts"]["failed"] == {"http_status": 409, "code": "generation_invalid_state"}
    assert lifecycle["contracts"]["deleting"] == {
        "http_status": 409,
        "code": "generation_invalid_state",
        "persisted_generation_status": "deleted",
    }
    assert lifecycle["contracts"]["missing_generation"]["http_status"] == 404
    assert lifecycle["contracts"]["wrong_kb"]["http_status"] == 404


def test_machine_review_is_explicitly_not_human_review() -> None:
    decision = json.loads((OUT / "manual_support_review_decisions.json").read_text(encoding="utf-8"))
    reviewer1 = _jsonl(OUT / "manual_support_review_reviewer1.jsonl")
    reviewer2 = _jsonl(OUT / "manual_support_review_reviewer2.jsonl")
    adjudicated = _jsonl(OUT / "manual_support_review_adjudicated.jsonl")
    assert decision["review_type"] == "multi_agent_machine_review"
    assert decision["human_review_performed"] is False
    assert decision["human_approval_claimed"] is False
    assert len(reviewer1) == len(reviewer2) == len(adjudicated) == decision["case_count"]
    assert all(row["review_type"] == "multi_agent_machine_review" for row in [*reviewer1, *reviewer2, *adjudicated])
    assert all(row["human_review_performed"] is False for row in adjudicated)
