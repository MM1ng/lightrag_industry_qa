from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("evaluation/phase10/conversation_retrieval_development_report.json")


def test_report_records_real_development_retrieval_metrics() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["status"] == "READY"
    assert report["dataset"]["case_count"] == 18
    assert report["dataset"]["development_only_guard"] is True
    assert report["rewrite"]["rewrite_accuracy"] == 1.0
    assert report["before"]["hit_recall_at_5"] == 0.6111111111111112
    assert report["after"]["hit_recall_at_5"] == 0.9444444444444444
    assert report["after"]["hit_recall_at_10"] == 1.0
    assert len(report["improved_cases"]) == 10
    assert len(report["unchanged_cases"]) == 5
    assert len(report["regressed_cases"]) == 3


def test_report_records_staging_fingerprint_and_test_status() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["fingerprint"]["knowledge_base_id"] == "8fce4626859d44abb70a9ae5b0372cea"
    assert report["fingerprint"]["generation_id"] == "g5162e7fb4208635103ff4ebb"
    assert report["fingerprint"]["retrieval_config"]["mode"] == "naive"
    assert report["fingerprint"]["retrieval_config"]["chunk_top_k"] == 20
