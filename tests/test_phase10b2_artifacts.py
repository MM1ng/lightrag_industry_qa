import json
from pathlib import Path


def test_phase10b2_artifacts_cover_required_splits_without_holdout_rerun():
    metrics = json.loads(Path("evaluation/phase10/final_grounding_metrics.json").read_text(encoding="utf-8"))
    assert metrics["holdout_rerun"] is False
    assert metrics["development"]["citation_trace_completeness"]["value"] == 1.0
    assert metrics["validation"]["negative_rejection_rate"]["value"] == 1.0


def test_phase10b2_failure_matrix_has_52_tunable_rows_and_12_historical_rows():
    summary = json.loads(Path("evaluation/phase10/answer_grounding_failure_summary.json").read_text(encoding="utf-8"))
    assert summary["development_validation_count"] == 52
    assert summary["historical_holdout_count"] == 12
    assert summary["holdout_rerun"] is False


def test_phase10b2_config_keeps_retrieval_frozen():
    config = json.loads(Path("evaluation/phase10/phase10b2_config_manifest.json").read_text(encoding="utf-8"))
    assert config["answer_grounding_enabled"] is True
    assert config["query_mode"] == "naive"
    assert config["top_k"] == 12
    assert config["chunk_top_k"] == 20
    assert config["rerank_enabled"] is False
    assert config["holdout_rerun"] is False
