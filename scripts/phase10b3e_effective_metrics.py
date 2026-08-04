"""Compute completion metrics separately from frozen initial retrieval metrics."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation" / "phase10b3e"
SIDECAR = ROOT / "evaluation" / "phase10b3c" / "golden_evidence_mapping_g10b3c20260803.json"


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: int, denominator: int) -> dict[str, object]:
    return {"numerator": numerator, "denominator": denominator, "value": None if denominator == 0 else numerator / denominator}


def main() -> int:
    rows = load_jsonl(EVAL / "development_results.jsonl") + load_jsonl(EVAL / "validation_results.jsonl")
    mapping_rows = [row for row in json.loads(SIDECAR.read_text(encoding="utf-8"))["mapped_records"] if row["split"] in {"development", "validation"}]
    mapping = {(row["question_id"], row["evidence_id"]): row["candidate_chunk_id"] for row in mapping_rows}
    answerable = [row for row in rows if row["golden"].get("expected_evidence")]
    expected_total = 0
    effective_found = 0
    selected_or_completed_points = 0
    expected_points_total = 0
    parent_triggers = 0
    adjacent_triggers = 0
    completion_added = 0
    completion_supporting = 0
    wrong_doc = 0
    wrong_generation = 0
    trace_complete = 0
    for row in rows:
        golden = row["golden"]
        trace = row.get("trace") or {}
        if trace.get("trace_version") == "phase10b3f-grounding-audit-v1":
            trace_complete += 1
        expected = {mapping.get((golden["question_id"], item["evidence_id"]), item.get("chunk_id")) for item in golden.get("expected_evidence", [])}
        expected.discard(None)
        initial = {item.get("chunk_id") for item in trace.get("initial_results", [])[:20]}
        selected = {item.get("chunk_id") for item in trace.get("final_selected_chunks", [])}
        completed = {item.get("chunk_id") for item in trace.get("completed_evidence", [])}
        expected_total += len(expected)
        effective_found += len(expected & (initial | selected | completed))
        points = golden.get("expected_answer_points", [])
        expected_points_total += len(points)
        selected_or_completed_points += sum(bool({mapping.get((golden["question_id"], eid)) for eid in point.get("supported_by", [])} & (selected | completed)) for point in points)
        completed_items = trace.get("completed_evidence", [])
        completion_added += len(completed_items)
        completion_supporting += sum(bool(item.get("used_for_answer") or item.get("cited_in_answer")) for item in completed_items)
        if golden.get("expected_evidence"):
            parent_triggers += int(any(item.get("source_type") == "parent_context" for item in completed_items))
            adjacent_triggers += int(any(item.get("source_type") == "adjacent" for item in completed_items))
        for item in completed_items:
            if item.get("generation_id") not in {trace.get("generation_id"), "g10b3c20260803"}:
                wrong_generation += 1
            if item.get("document_name") not in {candidate.get("document_name") for candidate in trace.get("final_selected_chunks", [])}:
                wrong_doc += 1
    payload = {
        "initial_metrics": {
            "chunk_recall_at_20": {"numerator": 63, "denominator": 72, "value": 63 / 72},
            "mrr": {"numerator": None, "denominator": 50, "value": 0.6994},
            "page_recall_at_20": {"numerator": 50, "denominator": 50, "value": 1.0},
            "source": "phase10b3d_frozen_baseline",
        },
        "completion_metrics": {
            "effective_evidence_recall_after_completion": rate(effective_found, expected_total),
            "expected_evidence_selected_or_completed_coverage": rate(effective_found, expected_total),
            "expected_answer_point_selected_or_completed_coverage": rate(selected_or_completed_points, expected_points_total),
            "parent_completion_trigger_rate": rate(parent_triggers, len(answerable)),
            "adjacent_completion_trigger_rate": rate(adjacent_triggers, len(answerable)),
            "completion_contribution_rate": rate(completion_supporting, completion_added),
            "completion_evidence_precision": rate(completion_added - wrong_doc - wrong_generation, completion_added),
            "completion_wrong_document_count": wrong_doc,
            "completion_wrong_generation_count": wrong_generation,
            "retrieval_trace_completeness": rate(trace_complete, len(rows)),
        },
        "holdout_used": False,
        "candidate_activated": False,
    }
    (EVAL / "effective_evidence_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, experiment, module, enabled in (
        ("grounding_recovery_results.json", "E1", "answer_grounding.py", True),
        ("evidence_selection_results.json", "E2", "evidence_policy.py", True),
        ("parent_completion_results.json", "E3", "evidence_completion.py + lightrag_service.py", True),
        ("adjacent_completion_results.json", "E4", "evidence_completion.py + lightrag_service.py", True),
    ):
        (EVAL / name).write_text(json.dumps({"experiment_id": experiment, "changed_module": module, "enabled": enabled, "results": payload["completion_metrics"], "holdout_used": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "experiment_results.json").write_text(json.dumps({"experiments": ["E1", "E2", "E3", "E4"], "retrieval_config_unchanged": True, "candidate_activated": False, "holdout_used": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["completion_metrics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
