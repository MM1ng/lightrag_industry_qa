"""Audit the historical Phase 12B-3A invalid Judge batches.

R1 did not persist provider raw responses.  This audit therefore reports the
recorded deterministic parser error and explicitly leaves unavailable raw
response fields as null instead of reconstructing or guessing them.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R1_SUMMARY = ROOT / "evaluation/phase12b3a_r1/r1_summary.json"
R1_DIFF = ROOT / "evaluation/phase12b3a_r1/semantic_judge_diff.jsonl"
OUTPUT = ROOT / "evaluation/phase12b3a_r2"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit_r1_invalid(
    summary_path: Path = R1_SUMMARY,
    diff_path: Path = R1_DIFF,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    invalid_ids = {
        str(item["question_id"])
        for item in summary.get("blocked_questions", [])
        if item.get("status") == "invalid_judge_response"
    }
    diff_rows = _read_jsonl(diff_path)
    invalid_ids.update(
        str(row.get("question_id") or "")
        for row in diff_rows
        if row.get("judge_status") == "invalid_judge_response" and row.get("question_id")
    )
    diff_by_question: dict[str, list[dict[str, Any]]] = {}
    for row in diff_rows:
        question_id = str(row.get("question_id") or "")
        if question_id in invalid_ids:
            diff_by_question.setdefault(question_id, []).append(row)

    audit_rows: list[dict[str, Any]] = []
    for question_id in sorted(invalid_ids):
        question_rows = diff_by_question.get(question_id, [])
        errors = sorted({str(row.get("judge_error") or "") for row in question_rows if row.get("judge_error")})
        subtype = "incomplete_matrix" if errors == [
            "judge response does not cover the complete claim/evidence matrix"
        ] else "other"
        audit_rows.append(
            {
                "source_run": "phase12b3a_r1",
                "question_id": question_id,
                "subtype": subtype,
                "raw_response_available": False,
                "raw_response": None,
                "evidence_basis": "recorded R1 deterministic parser error; provider raw response was not persisted",
                "parser_errors": errors,
                "prompt_token_estimate": None,
                "response_length_chars": None,
                "finish_reason": None,
                "expected_pair_count": len(question_rows),
                "returned_pair_count": None,
            }
        )
    return audit_rows, dict(sorted(Counter(row["subtype"] for row in audit_rows).items()))


def write_audit(
    output_dir: Path = OUTPUT,
    summary_path: Path = R1_SUMMARY,
    diff_path: Path = R1_DIFF,
) -> dict[str, Any]:
    rows, distribution = audit_r1_invalid(summary_path, diff_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "invalid_response_audit.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    summary = {
        "source_run": "phase12b3a_r1",
        "invalid_batch_count": len(rows),
        "raw_response_available_count": sum(bool(row["raw_response_available"]) for row in rows),
        "raw_response_unavailable_count": sum(not row["raw_response_available"] for row in rows),
        "subtype_distribution": distribution,
        "forensic_limit": "R1 did not persist provider raw responses; null fields are intentionally not reconstructed.",
    }
    (output_dir / "invalid_response_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(write_audit(), ensure_ascii=False))
