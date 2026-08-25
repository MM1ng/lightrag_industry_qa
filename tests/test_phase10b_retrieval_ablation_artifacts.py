import json
from pathlib import Path


def test_retrieval_ablation_artifact_has_no_holdout_and_records_unsupported_modes():
    path = Path("evaluation/phase10/retrieval_ablation_results.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["holdout_used_for_tuning"] is False
    assert payload["unsupported_requested_modes"]["dense"]["supported"] is False
    assert payload["unsupported_requested_modes"]["keyword"]["supported"] is False
    assert {run["split"] for run in payload["development_runs"]} == {"development"}
    assert payload["validation_selected_run"]["split"] == "validation"


def test_ablation_manifests_keep_single_variable_changes():
    root = Path("evaluation/phase10/experiments/retrieval_ablation")
    manifests = list(root.glob("*/development/experiment_manifest.json"))
    assert manifests
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert len(payload["changed_variables"]) == 1
        assert payload["holdout_used_for_tuning"] is False
