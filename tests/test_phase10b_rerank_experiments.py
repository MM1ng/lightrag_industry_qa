import json
from pathlib import Path


def test_rerank_artifact_preserves_disabled_contract_and_no_silent_fallback():
    payload = json.loads(Path("evaluation/phase10/rerank_results.json").read_text(encoding="utf-8"))
    disabled = next(item for item in payload["strategies"] if item["name"] == "disabled")
    assert payload["production_rerank_enabled"] is False
    assert payload["provider_scores_available"] is False
    assert payload["no_silent_success_fallback"] is True
    assert payload["selection"]["selected"] == "disabled"
    assert disabled["supported"] is True


def test_rerank_candidate_metrics_do_not_read_holdout():
    payload = json.loads(Path("evaluation/phase10/rerank_results.json").read_text(encoding="utf-8"))
    assert payload["holdout_used_for_tuning"] is False
    assert set(payload["splits"]) == {"development", "validation"}
