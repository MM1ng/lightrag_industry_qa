from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = PROJECT_ROOT / "evaluation" / "phase10" / "query_normalization_results.json"


def test_normalization_experiment_manifest_is_single_variable_and_dev_validation_only() -> None:
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    assert result["experiment_id"] == "phase10b-normalization-001"
    assert result["parent_experiment_id"] == "phase10a-real-baseline"
    assert result["changed_variable"] == "QA_QUERY_NORMALIZATION_ENABLED"
    assert result["retrieval_config_unchanged"] is True
    assert result["model_config_unchanged"] is True
    assert result["holdout_used_for_tuning"] is False
    assert result["holdout_rows_loaded"] is False
    assert result["retained_on_validation"] is True
    assert result["run_duration_seconds"] >= 0
    assert result["baseline"]["record_count"] == 52
    assert result["normalization"]["record_count"] == 52
    assert set(result["normalization"]["metrics_by_split"]) == {
        "development",
        "validation",
    }
    assert "metric_deltas_by_split" in result["normalization"]
    assert "latency_by_split" in result["normalization"]
    assert result["normalization"]["llm_call_count"] is None


def test_normalization_traces_record_metadata_without_changing_response_contract() -> None:
    rows = [
        json.loads(line)
        for line in (
            PROJECT_ROOT
            / "evaluation/phase10/experiments/query_normalization/development_validation_results.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(rows) == 52
    assert all(row["execution_status"] == "completed" for row in rows)
    assert all("detected_model" in row["trace"] for row in rows)
    assert all("added_aliases" in row["trace"] for row in rows)
    assert all("initial_results" in row["trace"] for row in rows)
    assert all("final_selected_chunks" in row["trace"] for row in rows)
    assert all("normalized_query" in row["trace"] for row in rows)
