"""Build the Phase 10B-2 answer-grounding matrix from saved results only."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from industrial_rag.answer_grounding import classify_question_type
from industrial_rag.phase10b_citation_binding import check_citation_binding
from industrial_rag.phase10b_refusal_analysis import classify_refusal_state


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _row(case: dict[str, Any], *, historical: bool = False) -> dict[str, Any]:
    binding = check_citation_binding(case)
    expected_points = case["golden"].get("expected_answer_points", [])
    cited = {item.get("chunk_id") for item in case.get("response", {}).get("citations", [])}
    expected_by_id = {item["evidence_id"]: item for item in case["golden"].get("expected_evidence", [])}
    supported_points = []
    unsupported_points = []
    for point in expected_points:
        evidence_chunks = {expected_by_id[eid]["chunk_id"] for eid in point.get("supported_by", []) if eid in expected_by_id}
        (supported_points if evidence_chunks & cited else unsupported_points).append(point["point_id"])
    failure_layer = "None"
    category = "none"
    if unsupported_points:
        failure_layer = "Generation"
        category = "unsupported_generation_claim"
    elif binding["wrong_page"]:
        failure_layer = "Citation"
        category = "wrong_page_citation"
    elif binding["wrong_chunk"]:
        failure_layer = "Citation"
        category = "wrong_chunk_citation"
    elif case["response"].get("status") == "insufficient_evidence" and case["golden"]["answerable"]:
        failure_layer = "Refusal"
        category = "answerable_but_over_refused"
    return {
        "question_id": case["question_id"],
        "split": case["golden"]["split"],
        "question_type": classify_question_type(case["golden"]["question"]),
        "answer_status": case["response"].get("status"),
        "expected_answer_points": expected_points,
        "expected_evidence": case["golden"].get("expected_evidence", []),
        "retrieved_evidence": (case.get("trace") or {}).get("initial_results", []),
        "selected_evidence": (case.get("trace") or {}).get("final_selected_chunks", []),
        "final_answer": case["response"].get("answer", ""),
        "final_citations": case["response"].get("citations", []),
        "supported_answer_points": supported_points,
        "unsupported_answer_points": unsupported_points,
        "missing_expected_points": unsupported_points,
        "wrong_document_citations": binding["wrong_document"],
        "wrong_page_citations": binding["wrong_page"],
        "wrong_chunk_citations": binding["wrong_chunk"],
        "incomplete_multi_evidence_citations": bool(expected_points) and bool(unsupported_points),
        "over_inference": bool(unsupported_points),
        "failure_layer": failure_layer,
        "failure_category": category,
        "failure_reason": category if category != "none" else "no deterministic grounding failure",
        "historical_holdout_analysis": historical,
        "retrieval_trace_present": case.get("trace") is not None,
        "refusal_state": classify_refusal_state(case),
    }


def main() -> int:
    paths = [
        Path("evaluation/phase10/grounding3/development/baseline_results.jsonl"),
        Path("evaluation/phase10/grounding3/validation/baseline_results.jsonl"),
    ]
    rows = [_row(case) for path in paths for case in _load(path)]
    rows.extend(_row(case, historical=True) for case in _load(Path("evaluation/phase10/holdout_results.jsonl")))
    output = Path("evaluation/phase10/answer_grounding_failure_matrix.jsonl")
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    groups: dict[str, dict[str, int]] = defaultdict(Counter)
    for row in rows:
        for field in ("split", "question_type", "failure_layer", "failure_category"):
            groups[field][str(row[field])] += 1
    summary = {
        "row_count": len(rows),
        "development_validation_count": sum(not row["historical_holdout_analysis"] for row in rows),
        "historical_holdout_count": sum(row["historical_holdout_analysis"] for row in rows),
        "groups": groups,
        "holdout_rerun": False,
    }
    Path("evaluation/phase10/answer_grounding_failure_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
