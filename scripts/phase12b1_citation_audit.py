"""Audit Phase 12B-1 citation failures from saved Development artifacts only."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
DIAGNOSIS = ROOT / "evaluation/phase12a/diagnosis_matrix.jsonl"
OUTPUT = ROOT / "evaluation/phase12b1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _citation_rows(point: dict[str, Any]) -> dict[str, Any]:
    citation = point.get("citation") or {}
    actual = list(dict.fromkeys(citation.get("actual_cited_chunk_ids") or []))
    supporting = list(dict.fromkeys(citation.get("supporting_actual_chunk_ids") or []))
    non_supporting = list(dict.fromkeys(citation.get("unsupported_actual_chunk_ids") or []))
    return {
        "expected_support_chunk_ids": list(point.get("expected_support_chunk_ids") or []),
        "actual_cited_chunk_ids": actual,
        "supporting_actual_chunk_ids": supporting,
        "unsupported_actual_chunk_ids": non_supporting,
        "citation_count": len(actual),
        "supporting_count": len(supporting),
        "non_supporting_count": len(non_supporting),
        "citation_precision": citation.get("citation_precision"),
        "citation_recall": citation.get("citation_recall"),
        "overcitation": bool(citation.get("overcitation")),
        "final_failure_stage": point.get("final_failure_stage"),
    }


def classify_rows(rows: list[dict[str, Any]]) -> tuple[str, str]:
    if any(item["overcitation"] and item["supporting_count"] > 0 for item in rows):
        return "over_citation", "supporting citation exists but one or more non-supporting citations are also attached"
    if any(item["supporting_count"] == 0 and item["citation_count"] > 0 for item in rows):
        return "wrong_citation", "the emitted answer point has citations but none is a supporting citation"
    if any(item["supporting_count"] == 0 and item["citation_count"] == 0 for item in rows):
        return "missing_citation", "the emitted answer point has no citation"
    if any(item["citation_count"] > len(set(item["actual_cited_chunk_ids"])) for item in rows):
        return "duplicate_citation", "the same citation chunk is repeated"
    return "unknown", "no deterministic subtype can be established from the saved fields"


def classify(points: list[dict[str, Any]]) -> tuple[str, str]:
    return classify_rows([_citation_rows(point) for point in points])


def main() -> int:
    dev_rows = load_jsonl(DEV_RESULTS)
    funnel_rows = load_jsonl(FUNNEL)
    diagnosis_rows = load_jsonl(DIAGNOSIS)
    if not all(row.get("split") == "development" for row in dev_rows + funnel_rows):
        raise SystemExit("refusing to mix non-Development rows")

    dev_by_id = {str(row["question_id"]): row for row in dev_rows}
    points_by_id: dict[str, list[dict[str, Any]]] = {}
    for point in funnel_rows:
        points_by_id.setdefault(str(point["question_id"]), []).append(point)
    failure_ids = [
        str(row["question_id"])
        for row in diagnosis_rows
        if row.get("primary_root_cause") == "citation_failure"
    ]

    audits: list[dict[str, Any]] = []
    subtypes: Counter[str] = Counter()
    for question_id in failure_ids:
        row = dev_by_id[question_id]
        points = points_by_id.get(question_id, [])
        subtype, diagnosis = classify(points)
        point_rows = [_citation_rows(point) for point in points]
        actual_ids = list(dict.fromkeys(
            citation.get("citation_id")
            for citation in row.get("response", {}).get("citations", [])
            if citation.get("citation_id")
        ))
        supporting_chunk_ids = list(dict.fromkeys(
            chunk_id
            for point in point_rows
            for chunk_id in point["supporting_actual_chunk_ids"]
        ))
        non_supporting_chunk_ids = list(dict.fromkeys(
            chunk_id
            for point in point_rows
            for chunk_id in point["unsupported_actual_chunk_ids"]
        ))
        subtype_counts = Counter()
        for item in point_rows:
            point_subtype, _ = classify_rows([item])
            subtype_counts[point_subtype] += 1
        audit = {
            "question_id": question_id,
            "question": row.get("golden", {}).get("question"),
            "answer": row.get("response", {}).get("answer"),
            "expected_supporting_evidence": row.get("golden", {}).get("expected_evidence", []),
            "actual_citation_ids": actual_ids,
            "supporting_citation_chunk_ids": supporting_chunk_ids,
            "non_supporting_citation_chunk_ids": non_supporting_chunk_ids,
            "citation_count": len(actual_ids),
            "primary_citation_subtype": subtype,
            "point_subtype_counts": dict(subtype_counts),
            "diagnosis": diagnosis,
            "points": point_rows,
            "answer_status": row.get("response", {}).get("status"),
        }
        audits.append(audit)
        subtypes[subtype] += 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "citation_failure_audit.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in audits) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "citation_failure_subtypes.json").write_text(
        json.dumps(
            {
                "source_split": "development",
                "audited_question_count": len(audits),
                "subtype_counts": dict(subtypes),
                "subtypes": [
                    "over_citation",
                    "wrong_citation",
                    "missing_citation",
                    "citation_scope_mismatch",
                    "duplicate_citation",
                    "parent_child_overcitation",
                    "unknown",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"audited_question_count": len(audits), "subtype_counts": dict(subtypes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
