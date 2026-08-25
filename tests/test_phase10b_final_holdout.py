import json
from pathlib import Path


def test_final_holdout_runs_once_after_freeze():
    manifest = json.loads(Path("evaluation/phase10/final_config_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frozen_before_holdout"] is True
    assert manifest["holdout_run_count"] == 1
    assert manifest["holdout_completed"] is True
    assert len(Path("evaluation/phase10/holdout_results.jsonl").read_text(encoding="utf-8").splitlines()) == 12


def test_final_config_keeps_candidate_chunking_out():
    manifest = json.loads(Path("evaluation/phase10/final_config_manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidate_chunking_run"] is False
