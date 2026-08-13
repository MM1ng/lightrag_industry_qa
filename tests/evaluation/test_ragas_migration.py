from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.ragas.conversation_adapter import (
    DATASET_PATH,
    build_ragas_dataset,
    dataset_fingerprint,
)
from evaluation.ragas.conversation_metrics import (
    score_retrieval_metrics,
    score_retrieval_metric_results,
)
from evaluation.ragas.migration_runner import (
    build_blocked_report,
    compare_with_canonical,
    no_metrics_in_blocked_report,
)


def test_ragas_dependency_and_required_api_are_available() -> None:
    import ragas
    from ragas import Dataset, Experiment, experiment
    from ragas.metrics import (
        IDBasedContextPrecision,
        IDBasedContextRecall,
        MetricResult,
        numeric_metric,
    )

    assert ragas.__version__ == "0.3.9"
    assert all((Dataset, Experiment, experiment, IDBasedContextPrecision, IDBasedContextRecall, MetricResult, numeric_metric))


def test_adapter_preserves_count_order_ids_and_fingerprint() -> None:
    bundle = build_ragas_dataset(DATASET_PATH)
    source_rows = [
        json.loads(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(bundle.cases) == 18
    assert [row["case_id"] for row in bundle.cases] == [row["case_id"] for row in source_rows]
    assert [row["reference_context_ids"] for row in bundle.dataset] == [
        row["gold_chunk_ids"] for row in source_rows
    ]
    assert len(bundle.dataset) == len(source_rows)
    assert bundle.fingerprint == dataset_fingerprint(DATASET_PATH, source_rows)


def test_adapter_rejects_validation_or_holdout_rows(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(
        json.dumps(
            {
                "case_id": "bad",
                "source_question_id": "V001",
                "history": [{"role": "user", "content": "x"}],
                "dependent_query": "x",
                "expected_standalone_query": "x",
                "gold_chunk_ids": ["gold"],
                "category": "guard",
                "split": "validation",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Development"):
        build_ragas_dataset(invalid)


def test_id_mapping_and_custom_metric_results_are_deterministic() -> None:
    retrieved = ["noise", "gold-2", "gold-2", "gold-1"]
    reference = ["gold-1", "gold-2"]

    scores = score_retrieval_metric_results(retrieved, reference)
    assert scores["hit_recall_at_5"].value == 1.0
    assert scores["mrr_at_5"].value == 0.5
    assert scores["hit_recall_at_10"].value == 1.0
    assert scores["mrr_at_10"].value == 0.5
    assert scores["mrr_at_5"].traces["output"]["first_gold_rank"] == 2


def test_recall_and_mrr_parity_at_5_and_10() -> None:
    scores = score_retrieval_metrics(
        ["noise", "gold-2", "gold-1"], ["gold-1", "gold-2"]
    )
    assert scores["evidence_recall_at_5"] == 1.0
    assert scores["evidence_recall_at_10"] == 1.0
    assert scores["mrr_at_5"] == 0.5
    assert scores["mrr_at_10"] == 0.5


def test_parity_checks_denominator_case_sets_and_all_six_metrics() -> None:
    old = {
        "dataset": {"case_count": 2},
        "before": {key: 0.5 for key in (
            "hit_recall_at_5", "evidence_recall_at_5", "mrr_at_5",
            "hit_recall_at_10", "evidence_recall_at_10", "mrr_at_10",
        )},
        "after": {key: 1.0 for key in (
            "hit_recall_at_5", "evidence_recall_at_5", "mrr_at_5",
            "hit_recall_at_10", "evidence_recall_at_10", "mrr_at_10",
        )},
        "improved_cases": ["a"],
        "unchanged_cases": ["b"],
        "regressed_cases": [],
        "cases": [{"case_id": "a"}, {"case_id": "b"}],
    }
    new = {
        "case_count": 2,
        "before": old["before"],
        "after": old["after"],
        "improved_cases": ["a"],
        "unchanged_cases": ["b"],
        "regressed_cases": [],
        "case_ids": ["a", "b"],
    }

    result = compare_with_canonical(old, new)
    assert result["passed"] is True
    assert result["denominator_parity"] is True
    assert result["case_classification_parity"] is True
    assert result["mismatches"] == []


def test_blocked_report_never_fabricates_metrics() -> None:
    report = build_blocked_report(
        reason_code="backend_unavailable",
        reason="Qdrant is unavailable",
        case_count=18,
        fingerprint={"rows_sha256": "abc"},
    )
    assert report["status"] == "BLOCKED"
    assert no_metrics_in_blocked_report(report)
    assert "before" not in report
    assert "after" not in report


def test_ragas_is_not_imported_by_production_sources() -> None:
    production_root = Path("src/industrial_rag")
    assert not list(production_root.rglob("*.py")) or not any(
        "ragas" in path.read_text(encoding="utf-8").lower()
        for path in production_root.rglob("*.py")
    )


def test_real_migration_report_has_persisted_experiment_rows() -> None:
    report_path = Path("evaluation/phase10/ragas_migration_development_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "MIGRATION_PASS"
    assert report["experiment_row_count"] == report["case_count"] == 18
    assert report["parity"]["mismatches"] == []
