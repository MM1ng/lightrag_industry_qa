from __future__ import annotations

import json
from pathlib import Path

from evaluation.phase10.conversation_e2e_contracts import JudgeConfig, fingerprint_dataset
from evaluation.phase10.conversation_e2e_runner import (
    build_blocked_report,
    build_report,
    render_markdown_report,
)


def test_blocked_report_contains_fingerprint_but_no_fabricated_metrics() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    report = build_blocked_report(fingerprint, "judge_unavailable", "semantic judge could not execute")

    assert report["status"] == "BLOCKED"
    assert report["dataset_fingerprint"]["case_count"] == 18
    assert "baseline" not in report
    assert "candidate" not in report


def test_report_preserves_case_order_and_markdown_contains_required_summary() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    rows = [{
        "case_id": "conv-s001",
        "gold_chunk_ids": ["g1"],
        "baseline": {"retrieved_chunk_ids": ["g1"]},
        "candidate": {"retrieved_chunk_ids": ["g1"]},
    }]
    report = build_report(
        cases=rows,
        fingerprint=fingerprint,
        runtime_fingerprint={"sha256": "runtime"},
        judge_config=JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "j", "fake", "e", 0.0, None, 60, 2, 1),
        semantic_rows=[{"case_id": "conv-s001", "baseline": {"faithfulness": None, "response_relevancy": None, "judge_error": "offline"}, "candidate": {"faithfulness": None, "response_relevancy": None, "judge_error": "offline"}}],
        experiment_artifact="evaluation/ragas/experiments/row.jsonl",
    )

    assert report["dataset_fingerprint"]["case_ids"] == ["conv-s001"]
    assert report["case_count"] == 1
    assert report["judge_errors"] == 2
    assert report["status"] == "BLOCKED"
    markdown = render_markdown_report(report)
    assert "Faithfulness" in markdown
    assert "Response Relevancy" in markdown
    assert "BLOCKED" in markdown or "R3_" in markdown
    json.dumps(report, ensure_ascii=False)


def test_report_marks_semantic_smoke_block_without_fabricating_case_scores() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    report = build_report(
        cases=[],
        fingerprint=fingerprint,
        runtime_fingerprint={"sha256": "runtime"},
        judge_config=JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "j", "fake", "e", 0.0, None, 60, 2, 1),
        semantic_rows=[],
        experiment_artifact="evaluation/ragas/experiments/row.jsonl",
        semantic_blocked_reason="judge smoke returned provider HTTP 500",
    )

    assert report["status"] == "BLOCKED"
    assert report["semantic_execution"] == {
        "status": "BLOCKED",
        "reason": "judge smoke returned provider HTTP 500",
        "formal_case_scoring_executed": False,
    }
    assert report["semantic_cases"] == []
    assert report["judge_errors"] == 1
