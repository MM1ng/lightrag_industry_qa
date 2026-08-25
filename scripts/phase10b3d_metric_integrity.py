"""Recompute Phase 10B-3D metrics and failure matrices from saved 52-case outputs."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from industrial_rag.phase10_evaluation import evaluate_retrieval

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("PHASE10_METRICS_OUTPUT", str(ROOT / "evaluation" / "phase10b3d")))
RESULT_ROOT = Path(os.environ.get("PHASE10_METRICS_RESULT_ROOT", str(ROOT / "evaluation" / "phase10b3a")))
RESULT_PATHS = [RESULT_ROOT / "development_results.jsonl", RESULT_ROOT / "validation_results.jsonl"]
SIDECAR = ROOT / "evaluation" / "phase10b3c" / "golden_evidence_mapping_g10b3c20260803.json"
REGISTRY = ROOT / "runtime" / "phase10b3c" / "kb_data" / "8fce4626859d44abb70a9ae5b0372cea" / "g10b3c20260803" / "context_registry"
DEFINITION_VERSION = "phase10b3d-metric-policy-v1"
ALLOWED = {"success", "partial_answer", "insufficient_evidence", "safety_blocked", "failed"}


def rate(numerator: int, denominator: int, *, included: list[str], excluded: list[str]) -> dict[str, object]:
    return {"numerator": numerator, "denominator": denominator, "value": None if denominator == 0 else numerator / denominator, "included_statuses": included, "excluded_statuses": excluded, "definition_version": DEFINITION_VERSION}


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [row for path in RESULT_PATHS for row in load_jsonl(path)]
    sidecar_rows = [row for row in json.loads(SIDECAR.read_text(encoding="utf-8"))["mapped_records"] if row["split"] in {"development", "validation"}]
    mapping = {(row["question_id"], row["evidence_id"]): row["candidate_chunk_id"] for row in sidecar_rows}
    chunks = {row["chunk_id"]: row for row in load_jsonl(REGISTRY / "chunks.jsonl")}
    parent_text = {row["parent_chunk_id"]: row.get("content", "") for row in load_jsonl(REGISTRY / "parents.jsonl")}
    statuses: Counter[tuple[str, str]] = Counter()
    matrix: list[dict[str, object]] = []
    false_rows: list[dict[str, object]] = []
    missing_rows: list[dict[str, object]] = []
    for row in rows:
        golden = row["golden"]
        response = row.get("response", {})
        status = response.get("status") if row.get("execution_status") == "completed" else "failed"
        if status not in ALLOWED:
            status = "failed"
        positive = bool(golden.get("expected_evidence"))
        statuses[("positive" if positive else "negative", status)] += 1
        expected = golden.get("expected_evidence", [])
        expected_ids = [mapping.get((golden["question_id"], item["evidence_id"]), item.get("chunk_id")) for item in expected]
        expected_set = {item for item in expected_ids if item}
        initial = row.get("trace", {}).get("initial_results", []) if row.get("trace") else []
        selected = row.get("trace", {}).get("final_selected_chunks", []) if row.get("trace") else []
        initial_ids = [item.get("chunk_id") for item in initial]
        selected_ids = [item.get("chunk_id") for item in selected]
        cited_ids = {item.get("chunk_id") for item in response.get("citations", [])}
        claims = response.get("claims", [])
        citation_ids = {item.get("citation_id") for item in response.get("citations", [])}
        supported_points = sum(bool(claim.get("citation_ids")) and set(claim.get("citation_ids", [])) <= citation_ids and bool(claim.get("evidence_ids")) for claim in claims)
        expected_points = golden.get("expected_answer_points", [])
        covered_points = sum(bool({mapping.get((golden["question_id"], eid)) for eid in point.get("supported_by", [])} & cited_ids) for point in expected_points)
        row_matrix = {
            "question_id": golden["question_id"], "split": row["split"], "question_type": golden.get("question_type"),
            "expected_evidence_count": len(expected), "expected_answer_point_count": len(expected_points), "status": status,
            "substantive_answer": status in {"success", "partial_answer"}, "claim_count": len(claims), "emitted_answer_point_count": len(claims),
            "supported_emitted_point_count": supported_points, "unsupported_emitted_point_count": len(claims) - supported_points,
            "covered_expected_point_count": covered_points, "missing_expected_point_count": len(expected_points) - covered_points,
            "citation_count": len(response.get("citations", [])), "evidence_panel_count": len(response.get("evidence", [])),
            "trace_available": row.get("trace") is not None, "first_expected_chunk_rank": next((i + 1 for i, item in enumerate(initial) if item.get("chunk_id") in expected_set), None),
            "expected_chunks_recalled_at_20": sorted(expected_set & set(initial_ids[:20])), "selected_expected_chunks": sorted(expected_set & set(selected_ids)),
            "completed_expected_chunks": sorted(expected_set & set(selected_ids)), "refusal_reason": "insufficient_evidence" if status == "insufficient_evidence" else None,
            "grounding_failure_categories": [],
        }
        matrix.append(row_matrix)
        if positive and status == "insufficient_evidence":
            recalled = expected_set & set(initial_ids[:20])
            selected_expected = expected_set & set(selected_ids)
            if not recalled:
                root_cause = "expected_chunk_not_recalled"
            elif not selected_expected:
                root_cause = "expected_chunk_recalled_not_selected"
            else:
                root_cause = "partial_evidence_misclassified_as_refusal"
            parent_available = any(chunks.get(cid, {}).get("parent_chunk_id") in parent_text for cid in expected_set)
            adjacent_available = any(chunks.get(cid, {}).get("previous_chunk_id") or chunks.get(cid, {}).get("next_chunk_id") for cid in expected_set)
            false_rows.append({"question_id": golden["question_id"], "split": row["split"], "expected_evidence": expected, "initial_rank": {cid: (initial_ids.index(cid) + 1 if cid in initial_ids else None) for cid in expected_set}, "selected_evidence": selected_ids, "completed_evidence": [], "parent_context_available": parent_available, "adjacent_context_available": adjacent_available, "answer_plan": row.get("trace", {}).get("answer_plan", []), "grounding_result": row.get("trace", {}).get("answer_plan", []), "final_status": status, "root_cause": root_cause})
        for item in expected:
            cid = mapping.get((golden["question_id"], item["evidence_id"]), item.get("chunk_id"))
            if cid not in set(initial_ids[:20]):
                chunk = chunks.get(cid, {})
                adjacent_text = ""
                for aid in (chunk.get("previous_chunk_id"), chunk.get("next_chunk_id")):
                    if aid in chunks:
                        adjacent_text += str(chunks[aid].get("content", ""))
                missing_rows.append({"question_id": golden["question_id"], "split": row["split"], "evidence_id": item["evidence_id"], "document": item["document_name"], "page": item["page_number"], "candidate_chunk_id": cid, "mapping_method": next((s["mapping_method"] for s in sidecar_rows if s["question_id"] == golden["question_id"] and s["evidence_id"] == item["evidence_id"]), None), "text_coverage": next((s["text_coverage_ratio"] for s in sidecar_rows if s["question_id"] == golden["question_id"] and s["evidence_id"] == item["evidence_id"]), None), "same_page_chunk_recalled": any(x.get("document_name") == item["document_name"] and x.get("page_number") == item["page_number"] for x in initial[:20]), "parent_contains_target": bool(chunk.get("parent_chunk_id") and item.get("evidence_text", "")[:40] in parent_text.get(chunk.get("parent_chunk_id"), "")), "adjacent_contains_target": item.get("evidence_text", "")[:40] in adjacent_text, "one_to_many_boundary": False, "sidecar_mapping_error": False})
    positives = [r for r in rows if r["golden"].get("expected_evidence")]
    negatives = [r for r in rows if not r["golden"].get("expected_evidence")]
    substantive = [r for r in rows if r.get("response", {}).get("status") in {"success", "partial_answer"} and r["golden"].get("expected_evidence")]
    unsupported = sum(not ({mapping.get((r["golden"]["question_id"], e["evidence_id"])) for e in r["golden"].get("expected_evidence", [])} & {c.get("chunk_id") for c in r["response"].get("citations", [])}) for r in substantive)
    citation_correct = len(substantive) - unsupported
    supported_emitted = sum(r["supported_emitted_point_count"] for r in matrix)
    metrics = {
        "definition_version": DEFINITION_VERSION, "positive_count": len(positives), "negative_count": len(negatives), "total_count": len(rows),
        "status_counts": {"positive": {status: statuses[("positive", status)] for status in sorted(ALLOWED)}, "negative": {status: statuses[("negative", status)] for status in sorted(ALLOWED)}},
        "retrieval": evaluate_retrieval(rows),
        "false_rejection_rate": rate(sum(r["response"].get("status") in {"insufficient_evidence", "safety_blocked"} for r in positives), len(positives), included=["insufficient_evidence", "safety_blocked"], excluded=["success", "partial_answer", "failed"]),
        "negative_rejection_rate": rate(sum(r["response"].get("status") in {"insufficient_evidence", "safety_blocked"} for r in negatives), len(negatives), included=["insufficient_evidence", "safety_blocked"], excluded=["success", "partial_answer", "failed"]),
        "question_level_unsupported_answer_rate": rate(unsupported, len(substantive), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "question_level_citation_accuracy": rate(citation_correct, len(substantive), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "claim_citation_exact_mapping_rate": rate(supported_emitted, sum(r["claim_count"] for r in matrix), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "emitted_answer_point_support_rate": rate(supported_emitted, sum(r["emitted_answer_point_count"] for r in matrix), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "unsupported_emitted_answer_point_rate": rate(sum(r["unsupported_emitted_point_count"] for r in matrix), sum(r["emitted_answer_point_count"] for r in matrix), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "expected_answer_point_coverage": rate(sum(r["covered_expected_point_count"] for r in matrix), sum(r["expected_answer_point_count"] for r in matrix), included=["success", "partial_answer", "insufficient_evidence"], excluded=["failed"]),
        "missing_expected_answer_point_rate": rate(sum(r["missing_expected_point_count"] for r in matrix), sum(r["expected_answer_point_count"] for r in matrix), included=["success", "partial_answer", "insufficient_evidence"], excluded=["failed"]),
        "evidence_panel_completeness": rate(sum(r["evidence_panel_count"] >= r["citation_count"] for r in matrix if r["substantive_answer"]), len(substantive), included=["success", "partial_answer"], excluded=["insufficient_evidence", "safety_blocked", "failed"]),
        "trace_completeness": rate(sum(r["trace_available"] for r in matrix if r["status"] != "failed"), sum(r["status"] != "failed" for r in matrix), included=["success", "partial_answer", "insufficient_evidence", "safety_blocked"], excluded=["failed"]),
        "table_trigger_rate": {"supported": False, "numerator": None, "denominator": None, "value": None, "reason": "no reliable table metadata in candidate artifacts"},
    }
    invariants = {"positive_count": len(positives) == 50, "negative_count": len(negatives) == 2, "total_count": len(rows) == 52, "positive_partition": sum(statuses[("positive", s)] for s in ALLOWED) == 50, "negative_partition": sum(statuses[("negative", s)] for s in ALLOWED) == 2, "substantive_answer_count": len(substantive) == statuses[("positive", "success")] + statuses[("positive", "partial_answer")], "unsupported_denominator": metrics["question_level_unsupported_answer_rate"]["denominator"] == len(substantive), "citation_denominator": metrics["question_level_citation_accuracy"]["denominator"] == len(substantive), "claim_mapping_denominator": metrics["claim_citation_exact_mapping_rate"]["denominator"] == sum(r["claim_count"] for r in matrix), "no_failed_hidden": sum(statuses[(kind, "failed")] for kind in ("positive", "negative")) == 0}
    invariant_payload = {"definition_version": DEFINITION_VERSION, "checks": invariants, "final_metrics_valid": all(invariants.values())}
    (OUT / "metric_policy.json").write_text(json.dumps({"definition_version": DEFINITION_VERSION, "substantive_statuses": ["success", "partial_answer"], "refusal_statuses": ["insufficient_evidence", "safety_blocked"], "failed_status": "failed", "positive_count": 50, "negative_count": 2}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "metric_invariant_check.json").write_text(json.dumps(invariant_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "recomputed_baseline_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "recomputed_case_statuses.jsonl").write_text("\n".join(json.dumps({"question_id": r["question_id"], "split": r["split"], "status": r["status"]}, ensure_ascii=False) for r in matrix) + "\n", encoding="utf-8")
    (OUT / "case_status_matrix.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in matrix) + "\n", encoding="utf-8")
    (OUT / "false_rejection_matrix.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in false_rows) + "\n", encoding="utf-8")
    (OUT / "false_rejection_summary.json").write_text(json.dumps({"count": len(false_rows), "by_root_cause": dict(Counter(r["root_cause"] for r in false_rows)), "rows": false_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "missing_chunk_analysis.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in missing_rows) + "\n", encoding="utf-8")
    (OUT / "experiment_results.json").write_text(json.dumps({"experiment_id": "phase10b3d-recompute-001", "code_commit": "2c25e41", "config_unchanged": True, "development_used_for_implementation": False, "validation_used_for_selection": False, "holdout_used": False, "quality_gate_passed": False, "reason": "Metric integrity and diagnosis only; no runtime variable changed."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"final_metrics_valid": invariant_payload["final_metrics_valid"], "false_rejection_count": len(false_rows), "missing_chunk_count": len(missing_rows), "substantive_count": len(substantive)}, ensure_ascii=False))
    return 0 if invariant_payload["final_metrics_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
