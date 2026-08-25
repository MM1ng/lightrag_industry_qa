from __future__ import annotations

from evaluation.phase10.conversation_e2e_metrics import (
    aggregate_arm,
    evaluate_gate,
    paired_case_counts,
    score_case,
)


def _case(before: dict, after: dict) -> dict:
    return {
        "case_id": "c1",
        "gold_chunk_ids": ["g1"],
        "baseline": before,
        "candidate": after,
    }


def _arm(ids: list[str], *, status: str = "success", citations: list[str] | None = None) -> dict:
    return {
        "retrieved_chunk_ids": ids,
        "answer_status": status,
        "citations": [{"chunk_id": item} for item in (citations or [])],
        "answer_points": [],
        "grounding_removed_points": [],
        "metric_error": None,
    }


def test_score_case_uses_ranked_ids_and_explicitly_marks_answer_gold_metrics_unavailable() -> None:
    result = score_case(_case(_arm(["noise", "g1"]), _arm(["g1"])))

    assert result["baseline"]["hit_recall_at_5"] == 1.0
    assert result["candidate"]["mrr_at_5"] == 1.0
    assert result["baseline"]["question_level_citation_accuracy"]["status"] == "metric_unavailable"
    assert result["candidate"]["expected_answer_coverage"]["status"] == "metric_unavailable"


def test_aggregate_and_paired_counts_preserve_all_cases() -> None:
    rows = [
        _case(_arm(["noise"]), _arm(["g1"])),
        {**_case(_arm(["g1"]), _arm(["g1"])), "case_id": "c2"},
    ]
    scored = [score_case(row) for row in rows]
    assert aggregate_arm(scored, "candidate")["denominator"] == 2
    assert paired_case_counts(scored) == {"improved": 1, "unchanged": 1, "regressed": 0}


def test_gate_returns_pass_only_when_guardrails_hold() -> None:
    def available(value: float) -> dict:
        return {"status": "available", "value": value, "denominator": 18}

    unavailable = {"status": "metric_unavailable", "value": None, "reason": "missing frozen gold"}
    summary = {
        "baseline": {
            "supporting_recall": unavailable,
            "false_rejection_rate": available(0.5),
            "question_level_citation_accuracy": unavailable,
            "unsupported_answer_rate": available(0.5),
            "expected_answer_coverage": unavailable,
        },
        "candidate": {
            "supporting_recall": unavailable,
            "false_rejection_rate": available(0.5),
            "question_level_citation_accuracy": unavailable,
            "unsupported_answer_rate": available(0.5),
            "expected_answer_coverage": unavailable,
        },
        "semantic": {"faithfulness": {"baseline_mean": 0.7, "candidate_mean": 0.7}, "response_relevancy": {"baseline_mean": 0.7, "candidate_mean": 0.7}},
        "semantic_execution": {"status": "READY"},
        "severe_regressions": [],
        "judge_errors": 0,
    }
    gate = evaluate_gate(summary)
    assert gate["status"] == "R3_MIXED"
    assert "supporting_recall_unavailable" in gate["reasons"]


def test_gate_does_not_pass_with_semantic_or_guardrail_regression() -> None:
    def available(value: float) -> dict:
        return {"status": "available", "value": value, "denominator": 18}

    unavailable = {"status": "metric_unavailable", "value": None, "reason": "missing frozen gold"}
    summary = {
        "baseline": {"supporting_recall": unavailable, "false_rejection_rate": available(0.0), "question_level_citation_accuracy": unavailable, "unsupported_answer_rate": available(0.0), "expected_answer_coverage": unavailable},
        "candidate": {"supporting_recall": unavailable, "false_rejection_rate": available(0.0), "question_level_citation_accuracy": unavailable, "unsupported_answer_rate": available(0.0), "expected_answer_coverage": unavailable},
        "semantic": {"faithfulness": {"baseline_mean": 0.8, "candidate_mean": 0.5}, "response_relevancy": {"baseline_mean": 0.8, "candidate_mean": 0.8}},
        "semantic_execution": {"status": "READY"},
        "severe_regressions": [],
        "judge_errors": 0,
    }
    assert evaluate_gate(summary)["status"] == "R3_FAIL"


def test_gate_blocks_semantic_execution_failure() -> None:
    summary = {
        "baseline": {},
        "candidate": {},
        "semantic": {},
        "semantic_execution": {"status": "BLOCKED", "reason": "provider unavailable"},
    }

    assert evaluate_gate(summary)["status"] == "BLOCKED"
