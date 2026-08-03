import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3e"


def _read(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def test_replay_uses_exact_52_saved_cases_and_does_not_pass_gate():
    payload = _read("replay_experiments.json")
    experiment = payload["experiments"][0]
    assert experiment["total_count"] == 52
    assert experiment["positive_count"] == 50
    assert experiment["negative_count"] == 2
    assert experiment["eligible_for_real_52_rerun"] is False


def test_replay_does_not_synthesize_answers_for_refusals():
    rows = [json.loads(line) for line in (OUT / "replay_baseline.jsonl").read_text(encoding="utf-8").splitlines()]
    refusal_rows = [row for row in rows if row["original_status"] == "insufficient_evidence"]
    assert refusal_rows
    assert all(row["replayable"] is False for row in refusal_rows)
    assert all(row["answer_points"] == [] for row in refusal_rows)


def test_completion_experiments_are_blocked_until_replay_gate():
    payload = _read("experiment_results.json")
    assert payload["status"] == "blocked_at_offline_replay_gate"
    assert payload["experiments_not_run"] == ["E1", "E2", "E3", "E4", "real-52-rerun"]
    assert payload["candidate_activated"] is False


def test_initial_metrics_are_not_claimed_as_completion_metrics():
    payload = _read("effective_evidence_metrics.json")
    assert payload["initial_metrics_unchanged"] is True
    assert payload["effective_evidence_recall_after_completion"]["value"] is None
    assert payload["completion_evidence_precision"]["value"] is None


def test_replay_secret_scan_is_clean():
    payload = _read("secret_scan.json")
    assert payload["confirmed_secret_count"] == 0
    assert payload["holdout_used"] is False
