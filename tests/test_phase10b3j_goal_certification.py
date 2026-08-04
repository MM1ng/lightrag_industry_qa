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
    assert metrics["input_evidence"]["golden_read"] is False
    assert metrics["input_evidence"]["holdout_read"] is False
    assert metrics["input_evidence"]["model_queries_made"] is False
    assert metrics["r2_non_regression_gates"]["passed"] is True
    assert metrics["r2_non_regression_gates"]["semantic_quality_non_regression"] == "not_assessed"


def test_lifecycle_fixture_keeps_active_pointer_and_blocks_terminal_states() -> None:
    lifecycle = json.loads((OUT / "lifecycle_contract_results.json").read_text(encoding="utf-8"))
    assert lifecycle["fixture"]["candidate_database_opened"] is False
    assert lifecycle["normal_queries_keep_active"] is True
    assert lifecycle["active_pointer_unchanged"] is True
    assert lifecycle["contracts"]["normal_query"]["http_status"] == 200
    assert lifecycle["contracts"]["building"]["http_status"] == 200
    assert lifecycle["contracts"]["failed"] == {"http_status": 409, "code": "generation_invalid_state"}
    assert lifecycle["contracts"]["deleting"] == {
        "http_status": 409,
        "code": "generation_invalid_state",
        "persisted_generation_status": "deleted",
    }


def test_machine_review_is_explicitly_not_human_review() -> None:
    decision = json.loads((OUT / "reviewer_decision.json").read_text(encoding="utf-8"))
    reviewer1 = _jsonl(OUT / "reviewer1_results.jsonl")
    reviewer2 = _jsonl(OUT / "reviewer2_results.jsonl")
    adjudicated = _jsonl(OUT / "adjudicated_results.jsonl")
    assert decision["review_type"] == "multi_agent_machine_review"
    assert decision["human_review_performed"] is False
    assert decision["human_approval_claimed"] is False
    assert len(reviewer1) == len(reviewer2) == len(adjudicated) == decision["case_count"]
    assert all(row["review_type"] == "multi_agent_machine_review" for row in [*reviewer1, *reviewer2, *adjudicated])
    assert all(row["human_review_performed"] is False for row in adjudicated)
