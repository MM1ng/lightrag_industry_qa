import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3g"


def test_generation_refusal_scope_and_no_policy_change():
    summary = json.loads((OUT / "generation_refusal_summary.json").read_text(encoding="utf-8"))
    assert summary["scope"] == {
        "split": "development",
        "total_questions": 36,
        "generation_refusal_count": 3,
        "validation_read": False,
        "holdout_read": False,
    }
    assert summary["policy_change_authorized"] is False
    assert summary["second_llm_call"] is False


def test_every_refusal_has_context_presence_record():
    rows = [json.loads(line) for line in (OUT / "generation_context_presence.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert {row["question_id"] for row in rows} == {"S007", "S020", "D005"}
    assert all(row["provider_invoked"] and row["provider_returned_refusal"] for row in rows)
    assert all(row["context_presence"] == "path_confirmed" for row in rows)
    assert all(row["context_payload_captured"] is False for row in rows)
    assert all(row["content_sufficiency"] == "indeterminate" for row in rows)


def test_matrix_keeps_expected_selected_and_citations():
    rows = [json.loads(line) for line in (OUT / "generation_refusal_matrix.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 3
    for row in rows:
        assert row["expected_evidence"]
        assert row["selected_evidence"]
        assert row["answer_status"] == "insufficient_evidence"
        assert row["failure_category"] == "refused_after_context_path_confirmed"
