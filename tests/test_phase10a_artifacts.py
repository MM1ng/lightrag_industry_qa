from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE10_DIR = PROJECT_ROOT / "evaluation" / "phase10"
EXPECTED_GENERATION_ID = "a2d1c77ce08b414495e9d845cc42f799"


def _load_jsonl(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (PHASE10_DIR / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested
            for item in value.values()
            for nested in _walk_keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _walk_keys(item)}
    return set()


def test_real_baseline_has_complete_same_generation_traces() -> None:
    results = _load_jsonl("baseline_results.jsonl")
    summary = json.loads((PHASE10_DIR / "baseline_summary.json").read_text("utf-8"))
    golden_path = PHASE10_DIR / "expanded_golden_set.jsonl"
    dataset_sha256 = hashlib.sha256(golden_path.read_bytes()).hexdigest()

    assert len(results) == 64
    assert summary["record_count"] == 64
    assert summary["completed_count"] == 64
    assert summary["trace_completeness"] == {
        "numerator": 64,
        "denominator": 64,
        "value": 1.0,
    }
    assert summary["cache_disabled_declared"] is True
    assert summary["missing_or_failed_question_ids"] == []

    request_ids: set[str] = set()
    for result in results:
        assert result["dataset_sha256"] == dataset_sha256
        assert result["execution_status"] == "completed"
        trace = result["trace"]
        assert trace["request_id"] == result["response"]["request_id"]
        assert trace["generation_id"] == EXPECTED_GENERATION_ID
        assert trace["trace_version"] == "phase10a-retrieval-trace-v1"
        assert trace["rerank_applied"] is False
        assert trace["reranked_results"] == []
        assert all(item["reranked_rank"] is None for item in trace["initial_results"])
        assert all(item["reranked_score"] is None for item in trace["initial_results"])
        assert all(
            set(item) >= {
                "final_rank",
                "chunk_id",
                "document_id",
                "document_name",
                "page_number",
                "initial_rank",
                "reranked_rank",
                "used_for_answer",
                "cited_in_answer",
            }
            for item in trace["final_selected_chunks"]
        )
        request_ids.add(trace["request_id"])
    assert len(request_ids) == 64


def test_real_trace_artifacts_exclude_forbidden_internal_fields() -> None:
    forbidden = {
        "authorization",
        "secret",
        "system_prompt",
        "full_prompt",
        "raw_vector",
        "local_path",
        "endpoint",
    }
    for result in _load_jsonl("baseline_results.jsonl"):
        assert not (_walk_keys(result["trace"]) & forbidden)


def test_frozen_metric_rates_keep_numerator_denominator_and_value() -> None:
    overall = json.loads(
        (PHASE10_DIR / "retrieval_metrics.json").read_text(encoding="utf-8")
    )["overall"]
    rate_names = {
        name
        for name in overall
        if name.startswith(
            (
                "chunk_recall_",
                "any_evidence_recall_",
                "complete_evidence_recall_",
                "document_recall_",
                "page_recall_",
            )
        )
    } | {
        "mrr",
        "graded_ndcg_at_10",
        "false_rejection_rate",
        "negative_rejection_rate",
        "unsupported_answer_rate",
        "question_level_citation_accuracy",
        "citation_trace_completeness",
    }
    assert rate_names
    for name in rate_names:
        assert set(overall[name]) >= {"numerator", "denominator", "value"}
        if overall[name]["denominator"] == 0:
            assert overall[name]["value"] is None

    assert overall["mrr"]["denominator"] == 60
    assert overall["negative_rejection_rate"]["denominator"] == 4
    assert overall["claim_level_citation_accuracy"] == {
        "available": False,
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }


def test_staging_secret_scan_covers_report_and_external_surfaces() -> None:
    scan = json.loads((PHASE10_DIR / "secret_scan.json").read_text(encoding="utf-8"))
    assert scan["credential_count"] == 2
    assert scan["credentials_configured"] is True
    assert scan["category_scanned_item_counts"]["api_responses"] >= 2
    assert scan["category_scanned_item_counts"]["ui_response"] >= 1
    assert scan["category_scanned_item_counts"]["logs"] >= 1
    assert scan["category_scanned_item_counts"]["database"] == 1
    assert scan["category_scanned_item_counts"]["report"] == 1
    assert scan["confirmed_secret_count"] == 0
    assert scan["passed"] is True
