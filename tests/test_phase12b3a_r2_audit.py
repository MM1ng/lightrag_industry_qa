from __future__ import annotations

import json

from scripts.phase12b3a_r2_audit_r1_invalid import audit_r1_invalid


def test_audit_records_r1_invalid_batches_without_inventing_missing_raw_fields(tmp_path) -> None:
    summary = tmp_path / "r1_summary.json"
    summary.write_text(
        json.dumps(
            {
                "blocked_questions": [
                    {"question_id": "D001", "status": "invalid_judge_response"},
                    {"question_id": "D002", "status": "no_final_claims"},
                ]
            }
        ),
        encoding="utf-8",
    )
    diff = tmp_path / "semantic_judge_diff.jsonl"
    diff.write_text(
        json.dumps(
            {
                "question_id": "D001",
                "claim_id": "P1",
                "evidence_id": "E1",
                "judge_status": "invalid_judge_response",
                "judge_error": "judge response does not cover the complete claim/evidence matrix",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows, distribution = audit_r1_invalid(summary, diff)

    assert len(rows) == 1
    assert rows[0]["question_id"] == "D001"
    assert rows[0]["subtype"] == "incomplete_matrix"
    assert rows[0]["raw_response_available"] is False
    assert rows[0]["raw_response"] is None
    assert rows[0]["returned_pair_count"] is None
    assert distribution == {"incomplete_matrix": 1}


def test_audit_recovers_invalid_batch_ids_from_diff_when_summary_omits_them(tmp_path) -> None:
    summary = tmp_path / "r1_summary.json"
    summary.write_text(json.dumps({}), encoding="utf-8")
    diff = tmp_path / "semantic_judge_diff.jsonl"
    diff.write_text(
        "\n".join(
            json.dumps(
                {
                    "question_id": question_id,
                    "judge_status": "invalid_judge_response",
                    "judge_error": "judge response does not cover the complete claim/evidence matrix",
                }
            )
            for question_id in ("D001", "D001", "D002")
        )
        + "\n",
        encoding="utf-8",
    )

    rows, distribution = audit_r1_invalid(summary, diff)

    assert [row["question_id"] for row in rows] == ["D001", "D002"]
    assert distribution == {"incomplete_matrix": 2}
