from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("evaluation/phase10/conversation_retrieval_development_report.json")


def test_report_is_explicitly_blocked_without_fabricated_retrieval_metrics() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["status"] == "BLOCKED"
    assert report["blocker"]["reason_code"] == "qdrant_unavailable"
    assert report["dataset"]["case_count"] == 18
    assert report["dataset"]["development_only_guard"] is True
    assert report["rewrite"]["rewrite_accuracy"] == 1.0
    assert report["before"] is None
    assert report["after"] is None
    assert report["delta"] is None
    assert report["regressed_cases"] == []


def test_report_records_staging_fingerprint_and_test_status() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["fingerprint"]["knowledge_base_id"] == "8fce4626859d44abb70a9ae5b0372cea"
    assert report["fingerprint"]["generation_id"] == "g5162e7fb4208635103ff4ebb"
    assert report["fingerprint"]["query_mode"] == "naive"
    assert report["test_results"]["focused_evaluator_tests"] == "9 passed"
