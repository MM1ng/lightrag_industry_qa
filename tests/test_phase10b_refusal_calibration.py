import json
from pathlib import Path

from industrial_rag.phase10b_refusal_analysis import classify_refusal_state


def test_refusal_states_are_explainable():
    case = {"golden": {"answerable": True, "expected_evidence": []}, "response": {"status": "insufficient_evidence"}}
    assert classify_refusal_state(case) == "insufficient_evidence"


def test_refusal_artifact_does_not_change_threshold_or_read_holdout():
    payload = json.loads(Path("evaluation/phase10/refusal_calibration_results.json").read_text(encoding="utf-8"))
    assert payload["thresholds_modified"] is False
    assert payload["holdout_used_for_tuning"] is False
    assert set(payload["splits"]) == {"development", "validation"}
