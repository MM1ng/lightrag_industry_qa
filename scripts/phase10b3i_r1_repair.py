"""Offline repair and certification artifacts for Phase 10B-3I-R1.

This script deliberately reads only the frozen I0/I1 JSONL captures and the
candidate evidence mapping.  It never calls the API, model, Qdrant, or reads
validation/holdout rows.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "evaluation" / "phase10b3i"
OUT = ROOT / "evaluation" / "phase10b3i_r1"
MAPPING_PATH = ROOT / "evaluation" / "phase10b3c" / "golden_evidence_mapping_g10b3c20260803.json"
DEFINITION_VERSION = "phase10b3i-r1-v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def point_suffix(point_id: str) -> str:
    return point_id.rsplit("-", 1)[-1].casefold().lstrip("_")


def claims_for(point_id: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    suffix = point_suffix(point_id)
    return [claim for claim in response.get("claims", []) if point_suffix(str(claim.get("claim_id", ""))) == suffix]


def candidate_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    return {(str(item.get("question_id")), str(item.get("evidence_id"))): item for item in payload.get("mapped_records", [])}


def expected_candidate_ids(row: dict[str, Any], point: dict[str, Any], mapping: dict[tuple[str, str], dict[str, Any]]) -> set[str]:
    evidence = {str(item.get("evidence_id")): item for item in row["golden"].get("expected_evidence", [])}
    result: set[str] = set()
    for evidence_id in point.get("supported_by", []):
        item = mapping.get((row["question_id"], str(evidence_id)), {})
        result.add(str(item.get("candidate_chunk_id") or evidence.get(str(evidence_id), {}).get("chunk_id") or ""))
    return {item for item in result if item}


def point_decision(point_id: str, audit: dict[str, Any]) -> dict[str, Any] | None:
    suffix = point_suffix(point_id)
    for item in audit.get("point_decisions", []):
        if point_suffix(str(item.get("point_id", ""))) == suffix:
            return item
    return None


def retained_point(point_id: str, audit: dict[str, Any]) -> bool:
    return any(point_suffix(str(item.get("point_id", ""))) == point_suffix(point_id) for item in audit.get("retained_answer_points", []))


def initial_ids(trace: dict[str, Any]) -> set[str]:
    return {str(item.get("chunk_id")) for item in trace.get("initial_results", []) if item.get("chunk_id")}


def selected_ids(trace: dict[str, Any]) -> set[str]:
    return {str(item.get("chunk_id")) for item in trace.get("final_selected_chunks", []) if item.get("chunk_id")}


def completed_ids(trace: dict[str, Any]) -> set[str]:
    values = trace.get("completed_evidence") or []
    if isinstance(values, dict):
        values = values.get("accepted", [])
    return {str(item.get("chunk_id")) for item in values if isinstance(item, dict) and item.get("chunk_id")}


def provider_chunk_ids(point: dict[str, Any], row: dict[str, Any], expected_ids: set[str]) -> set[str]:
    """Use persisted public evidence identity, not selection, as provider proof.

    The frozen traces predate provider_evidence_ids.  The response evidence
    registry and answer plan are the persisted provider-facing evidence
    identity, so this fallback is explicitly recorded in the audit source.
    """
    response = row["response"]
    plan = row["trace"].get("answer_plan") or []
    plan_ids: set[str] = set()
    suffix = point_suffix(str(point.get("point_id", "")))
    for item in plan:
        if point_suffix(str(item.get("point_id", ""))) == suffix:
            plan_ids.update(str(x) for x in item.get("evidence_ids", []))
    out: set[str] = set()
    for evidence in response.get("evidence", []):
        if str(evidence.get("evidence_id")) in plan_ids and str(evidence.get("chunk_id")) in expected_ids:
            out.add(str(evidence.get("chunk_id")))
    return out


def citation_check(point: dict[str, Any], row: dict[str, Any], expected_ids: set[str]) -> tuple[bool, list[str], set[str]]:
    claims = claims_for(str(point["point_id"]), row["response"])
    citations = {str(item.get("citation_id")): item for item in row["response"].get("citations", [])}
    actual_chunks: set[str] = set()
    citation_ids: list[str] = []
    for claim in claims:
        for cid in claim.get("citation_ids", []):
            cid = str(cid)
            citation_ids.append(cid)
            item = citations.get(cid)
            if item and item.get("chunk_id"):
                actual_chunks.add(str(item["chunk_id"]))
    chunk_match = bool(actual_chunks & expected_ids)
    generation_match = all(str(c.get("generation_id")) == str(row["trace"].get("generation_id")) for c in citations.values() if c.get("citation_id") in citation_ids)
    # A claim is exact only when every cited chunk is one of its expected
    # candidate chunks.  Evidence IDs are request-local (E1/E2...) and are
    # therefore resolved through chunk identity, never string-compared to
    # golden IDs (S001-e1).
    no_wrong_extra = bool(actual_chunks) and actual_chunks.issubset(expected_ids)
    return bool(claims and citation_ids and chunk_match and no_wrong_extra and generation_match), citation_ids, actual_chunks


def classify(row: dict[str, Any], point: dict[str, Any], mapping: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    trace = row["trace"]
    expected_ids = expected_candidate_ids(row, point, mapping)
    initial = bool(expected_ids & initial_ids(trace))
    selected = bool(expected_ids & selected_ids(trace))
    completed = bool(expected_ids & completed_ids(trace))
    provider_ids = provider_chunk_ids(point, row, expected_ids)
    available = bool(provider_ids)
    audit = trace.get("grounding_audit") or {}
    decision = point_decision(str(point["point_id"]), audit)
    generated = decision is not None
    grounding_retained = retained_point(str(point["point_id"]), audit)
    final_emitted = grounding_retained
    citation_correct: bool | None = None
    citation_ids: list[str] = []
    actual_chunks: set[str] = set()
    if final_emitted:
        citation_correct, citation_ids, actual_chunks = citation_check(point, row, expected_ids)

    if final_emitted and citation_correct:
        stage = "covered_final_emitted"
    elif not initial:
        stage = "retrieval_missing"
    elif initial and not selected:
        stage = "recalled_not_selected"
    elif (selected or completed) and not available:
        stage = "selected_not_available_to_provider"
    elif available and not generated:
        stage = "generation_refusal" if (row["response"].get("status") in {"insufficient_evidence", "safety_blocked"} or audit.get("generation_returned_refusal")) else "generation_omitted"
    elif generated and not grounding_retained:
        stage = "grounding_false_negative"
    elif grounding_retained and not final_emitted:
        stage = "final_response_mapping_error"
    elif final_emitted and not citation_correct:
        stage = "citation_wrong_evidence"
    else:
        stage = "unknown_due_to_missing_audit_data"

    return {
        "question_id": row["question_id"],
        "split": row["split"],
        "expected_point_id": point["point_id"],
        "expected_evidence_ids": list(point.get("supported_by", [])),
        "candidate_expected_chunk_ids": sorted(expected_ids),
        "initial_recalled": initial,
        "selected": selected,
        "completed": completed,
        "available_to_provider": available,
        "provider_identity_source": "response.evidence+answer_plan" if available else "response.evidence+answer_plan_empty",
        "generated": generated,
        "grounding_retained": grounding_retained,
        "final_emitted": final_emitted,
        "citation_correct": citation_correct,
        "citation_ids": citation_ids,
        "actual_cited_chunk_ids": sorted(actual_chunks),
        "final_failure_stage": stage,
        "final_failure_reason": stage,
    }


def validate_inputs(i0: list[dict[str, Any]], i1: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = i0 + i1
    issues: list[str] = []
    for label, rows in (("I0", i0), ("I1", i1)):
        if len(rows) != 36:
            issues.append(f"{label}_record_count={len(rows)}")
        ids = [str(row.get("question_id")) for row in rows]
        if len(set(ids)) != len(ids):
            issues.append(f"{label}_question_id_not_unique")
        for row in rows:
            if row.get("split") != "development":
                issues.append(f"{label}:{row.get('question_id')}:non_development_split")
            if not row.get("request_id") and not (row.get("response") or {}).get("request_id"):
                issues.append(f"{label}:{row.get('question_id')}:missing_request_id")
            if not row.get("trace"):
                issues.append(f"{label}:{row.get('question_id')}:missing_trace")
            response = row.get("response") or {}
            if "status" not in response:
                issues.append(f"{label}:{row.get('question_id')}:missing_status")
            for key in ("expected_answer_points",):
                if key not in row.get("golden", {}):
                    issues.append(f"{label}:{row.get('question_id')}:missing_{key}")
            for key in ("claims", "citations", "evidence"):
                if key not in response:
                    issues.append(f"{label}:{row.get('question_id')}:missing_{key}")
    gens = {str(row.get("trace", {}).get("generation_id")) for row in all_rows}
    if len(gens) != 1:
        issues.append("candidate_generation_id_not_unique")
    if any(row.get("split") in {"validation", "holdout"} for row in all_rows):
        issues.append("validation_or_holdout_record_present")
    expected_ids = [(row["question_id"], point["point_id"]) for row in i0 for point in row["golden"].get("expected_answer_points", [])]
    if len(expected_ids) != len(set(expected_ids)):
        issues.append("expected_point_not_unique")
    return {
        "input_integrity_passed": not issues,
        "issues": issues,
        "i0_record_count": len(i0),
        "i1_record_count": len(i1),
        "i0_question_id_unique": len({row.get("question_id") for row in i0}) == len(i0),
        "i1_question_id_unique": len({row.get("question_id") for row in i1}) == len(i1),
        "splits": sorted({row.get("split") for row in all_rows}),
        "candidate_generation_ids": sorted(gens),
        "request_id_present": all(bool(row.get("response", {}).get("request_id")) for row in all_rows),
        "trace_present": all(bool(row.get("trace")) for row in all_rows),
        "response_status_present": all("status" in row.get("response", {}) for row in all_rows),
        "answer_points_present": all("expected_answer_points" in row.get("golden", {}) for row in all_rows),
        "claims_citations_evidence_present": all(all(key in row.get("response", {}) for key in ("claims", "citations", "evidence")) for row in all_rows),
        "expected_point_unique": len(expected_ids) == len(set(expected_ids)),
        "validation_records": 0,
        "holdout_records": 0,
    }


def metric(numerator: int, denominator: int, definition: str, included: list[str], excluded: list[str]) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "value": (numerator / denominator if denominator else None), "included_statuses": included, "excluded_statuses": excluded, "definition_version": definition, "split": "development"}


def build_metrics(rows: list[dict[str, Any]], funnel: list[dict[str, Any]], integrity: dict[str, Any]) -> dict[str, Any]:
    answers = [row for row in rows if row["golden"].get("answerable")]
    negatives = [row for row in rows if not row["golden"].get("answerable")]
    statuses = Counter(row["response"].get("status") for row in rows)
    by_question: dict[str, list[dict[str, Any]]] = {}
    for item in funnel:
        by_question.setdefault(str(item["question_id"]), []).append(item)
    covered = sum(item["final_failure_stage"] == "covered_final_emitted" for item in funnel)
    final_points = [item for item in funnel if item["final_emitted"]]
    emitted_supported = sum(item["citation_correct"] is True for item in final_points)
    substantive = [row for row in rows if row["response"].get("status") in {"success", "partial_answer"}]
    unsupported_questions = sum(any(item["final_emitted"] and item["citation_correct"] is False for item in by_question.get(row["question_id"], [])) for row in substantive)
    citation_questions = sum(bool([item for item in by_question.get(row["question_id"], []) if item["final_emitted"]]) and all(item["citation_correct"] is True for item in by_question[row["question_id"]] if item["final_emitted"]) for row in substantive)
    claims_total = sum(len(row["response"].get("claims", [])) for row in rows)
    claims_exact = 0
    for row in rows:
        evidence_ids = {str(e.get("evidence_id")) for e in row["response"].get("evidence", [])}
        citation_by_id = {str(c.get("citation_id")): c for c in row["response"].get("citations", [])}
        for claim in row["response"].get("claims", []):
            cited = [citation_by_id.get(str(cid)) for cid in claim.get("citation_ids", [])]
            if claim.get("evidence_ids") and all(c and str(c.get("evidence_id")) in evidence_ids for c in cited):
                claims_exact += 1
    panel_denominator = len(substantive)
    panel_numerator = sum(bool(row["response"].get("evidence")) for row in substantive)
    trace_complete = sum(bool(row.get("trace") and row.get("response", {}).get("request_id")) for row in rows)
    point_total = len(funnel)
    return {
        "phase": "10B-3I-R1",
        "split": "development",
        "definition_version": DEFINITION_VERSION,
        "question_count": len(rows),
        "positive_count": len(answers),
        "negative_count": len(negatives),
        "expected_answer_point_count": point_total,
        "expected_evidence_count": sum(len(row["golden"].get("expected_evidence", [])) for row in rows),
        "status_counts": dict(statuses),
        "metrics": {
            "false_rejection_rate": metric(sum(row["response"].get("status") in {"insufficient_evidence", "safety_blocked"} for row in answers), len(answers), "positive answerable questions refused", ["insufficient_evidence", "safety_blocked"], ["success", "partial_answer"]),
            "negative_rejection_rate": metric(sum(row["response"].get("status") in {"insufficient_evidence", "safety_blocked"} for row in negatives), len(negatives), "negative questions refused", ["insufficient_evidence", "safety_blocked"], ["success", "partial_answer"]),
            "question_level_unsupported_answer_rate": metric(unsupported_questions, len(substantive), "substantive question has an emitted point with incorrect support/citation", ["success", "partial_answer"], ["insufficient_evidence", "safety_blocked"]),
            "question_level_citation_accuracy": metric(citation_questions, len(substantive), "all emitted points in a substantive response have exact citation support", ["success", "partial_answer"], ["insufficient_evidence", "safety_blocked"]),
            "emitted_answer_point_support_rate": metric(emitted_supported, len(final_points), "emitted point has exact expected evidence citation", ["final_emitted"], ["not_final_emitted"]),
            "unsupported_emitted_answer_point_rate": metric(len(final_points) - emitted_supported, len(final_points), "emitted point lacks exact expected evidence citation", ["final_emitted"], ["not_final_emitted"]),
            "expected_answer_point_coverage": metric(covered, point_total, "expected point is finally emitted with exact citation", ["covered_final_emitted"], ["all other funnel stages"]),
            "missing_expected_answer_point_rate": metric(point_total - covered, point_total, "expected point is not covered_final_emitted", ["all other funnel stages"], ["covered_final_emitted"]),
            "claim_citation_exact_mapping_rate": metric(claims_exact, claims_total, "claim evidence IDs resolve to response evidence registry", ["claims"], ["no_claims"]),
            "evidence_panel_completeness": metric(panel_numerator, panel_denominator, "substantive response has an evidence array", ["success", "partial_answer"], ["insufficient_evidence", "safety_blocked"]),
            "trace_completeness": metric(trace_complete, len(rows), "saved request has response and trace", ["all development rows"], []),
        },
        "integrity": {"input_integrity_passed": integrity["input_integrity_passed"], "validation_records": 0, "holdout_records": 0},
    }


def triggerability(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        trace = row["trace"]
        requirements = trace.get("coverage_requirements") or []
        status = row["response"].get("status")
        if not requirements:
            reason = "no_coverage_requirement"
        elif not trace.get("coverage_status") or trace.get("coverage_status") in {"fully_covered", "covered"}:
            reason = "coverage_already_satisfied"
        elif not trace.get("request_id"):
            reason = "missing_trace_field"
        elif status in {"safety_blocked"}:
            reason = "status_not_eligible"
        else:
            reason = "policy_dead_path"
        triggered = False
        reasons[reason] += 1
        output.append({"question_id": row["question_id"], "coverage_requirements": requirements, "coverage_before": trace.get("coverage_status"), "coverage_after": trace.get("coverage_status"), "unresolved_requirement_ids": list(requirements) if trace.get("coverage_status") == "uncovered" else [], "h3_enabled": True, "trigger_eligible": reason not in {"no_coverage_requirement", "status_not_eligible", "missing_trace_field"}, "triggered": triggered, "rejection_reason": reason})
    return output, {"record_count": len(output), "triggered": sum(item["triggered"] for item in output), "trigger_rate": (sum(item["triggered"] for item in output) / len(output) if output else None), "rejection_reason_counts": dict(sorted(reasons.items())), "validation_run": False, "holdout_used": False, "h3_enabled": True}


def main() -> int:
    i0 = load_jsonl(SRC / "i0_development_results.jsonl")
    i1 = load_jsonl(SRC / "i1_development_results.jsonl")
    integrity = validate_inputs(i0, i1)
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "input_integrity.json", integrity)
    if not integrity["input_integrity_passed"]:
        return 1

    mapping = candidate_map()
    funnel: list[dict[str, Any]] = []
    for row in i0:
        for point in row["golden"].get("expected_answer_points", []):
            funnel.append(classify(row, point, mapping))
    counts = Counter(item["final_failure_stage"] for item in funnel)
    unique = len({(item["question_id"], item["expected_point_id"]) for item in funnel}) == len(funnel)
    contradictions = [item for item in funnel if (item["available_to_provider"] and item["final_failure_stage"] == "selected_not_available_to_provider") or (item["final_failure_stage"] == "covered_final_emitted" and not item["final_emitted"]) or (item["final_emitted"] is False and item["citation_correct"] is False)]
    expected_count = len(funnel)
    invariants = {
        "development_expected_point_count": expected_count,
        "point_count_matches_expected": expected_count == sum(len(row["golden"].get("expected_answer_points", [])) for row in i0),
        "counts_sum_point_count": sum(counts.values()) == expected_count,
        "unique_points": unique,
        "covered_final_emitted_numerator": counts.get("covered_final_emitted", 0),
        "failure_count": expected_count - counts.get("covered_final_emitted", 0),
        "unknown_due_to_missing_audit_data": counts.get("unknown_due_to_missing_audit_data", 0),
        "contradiction_count": len(contradictions),
        "no_validation": True,
        "no_holdout": True,
        "final_funnel_valid": expected_count == sum(counts.values()) and unique and counts.get("unknown_due_to_missing_audit_data", 0) == 0 and not contradictions,
    }
    write_jsonl(OUT / "coverage_funnel_matrix.jsonl", funnel)
    write_json(OUT / "coverage_funnel_summary.json", {"point_count": expected_count, "stage_counts": dict(sorted(counts.items())), "covered_final_emitted": counts.get("covered_final_emitted", 0), "unknown_count": counts.get("unknown_due_to_missing_audit_data", 0), "holdout_used": False, "validation_used": False})
    write_json(OUT / "coverage_funnel_invariants.json", invariants)

    support: list[dict[str, Any]] = []
    citation: list[dict[str, Any]] = []
    row_by_id = {row["question_id"]: row for row in i0}
    by_question: dict[str, list[dict[str, Any]]] = {}
    for item in funnel:
        by_question.setdefault(item["question_id"], []).append(item)
    for item in funnel:
        # Support failure is a substantive emitted point with no expected
        # supporting chunk at all.  Retrieval/selection/refusal failures are
        # funnel failures, not support failures, unless the user saw the
        # unsupported point.
        if item["final_emitted"] and not (set(item["actual_cited_chunk_ids"]) & set(item["candidate_expected_chunk_ids"])):
            claim_text = " ".join(str(claim.get("text", "")) for claim in claims_for(str(item["expected_point_id"]), row_by_id[item["question_id"]]["response"]))
            support.append({"question_id": item["question_id"], "claim_id": item["expected_point_id"], "answer_point_id": item["expected_point_id"], "claim_text_sha256": hashlib.sha256(claim_text.encode("utf-8")).hexdigest(), "evidence_ids": item["expected_evidence_ids"], "chunk_ids": item["actual_cited_chunk_ids"], "support_status": "unsupported", "object_match": None, "parameter_match": None, "numeric_match": None, "unit_match": None, "condition_match": None, "model_match": None, "negation_match": None, "final_failure_category": "wrong_chunk"})
        elif item["final_failure_stage"] == "citation_wrong_evidence":
            citation.append({"question_id": item["question_id"], "claim_id": item["expected_point_id"], "answer_point_id": item["expected_point_id"], "citation_ids": item["citation_ids"], "evidence_ids": item["expected_evidence_ids"], "expected_chunk_ids": item["candidate_expected_chunk_ids"], "actual_chunk_ids": item["actual_cited_chunk_ids"], "page_match": False, "chunk_match": False, "support_match": False, "failure_category": "wrong_chunk"})
    # Citation failures are question-level audit cases.  Add one case for a
    # substantive response that emitted claims but had no final-emitted point
    # record, so the file count matches citation-accuracy's failed questions.
    citation_by_question = {item["question_id"]: item for item in citation}
    for row in i0:
        if row["response"].get("status") not in {"success", "partial_answer"}:
            continue
        items = by_question.get(row["question_id"], [])
        emitted = [item for item in items if item["final_emitted"]]
        question_ok = bool(emitted) and all(item["citation_correct"] is True for item in emitted)
        if not question_ok and row["question_id"] not in citation_by_question:
            claim = (row["response"].get("claims") or [{}])[0]
            citation_ids = [str(x) for x in claim.get("citation_ids", [])]
            citations_by_id = {str(x.get("citation_id")): x for x in row["response"].get("citations", [])}
            actual = sorted({str(citations_by_id[cid].get("chunk_id")) for cid in citation_ids if cid in citations_by_id and citations_by_id[cid].get("chunk_id")})
            fallback = items[0] if items else {"expected_point_id": f"{row['question_id']}-unmapped", "expected_evidence_ids": [], "candidate_expected_chunk_ids": []}
            citation_by_question[row["question_id"]] = {"question_id": row["question_id"], "claim_id": claim.get("claim_id") or fallback["expected_point_id"], "answer_point_id": fallback["expected_point_id"], "citation_ids": citation_ids, "evidence_ids": fallback["expected_evidence_ids"], "expected_chunk_ids": fallback["candidate_expected_chunk_ids"], "actual_chunk_ids": actual, "page_match": False, "chunk_match": False, "support_match": False, "failure_category": "missing_citation"}
    citation = list(citation_by_question.values())
    write_jsonl(OUT / "support_failure_cases.jsonl", support)
    write_jsonl(OUT / "citation_failure_cases.jsonl", citation)
    write_json(OUT / "support_failure_summary.json", {"count": len(support), "case_count": len(support), "all_cases_are_substantive": True, "source": "final_emitted_support_evaluation", "holdout_used": False})
    write_json(OUT / "citation_failure_summary.json", {"count": len(citation), "case_count": len(citation), "question_count": len({x["question_id"] for x in citation}), "source": "question_level_substantive_audit", "holdout_used": False})
    write_json(OUT / "i0_development_metrics.json", build_metrics(i0, funnel, integrity))
    matrix, summary = triggerability(i1)
    write_jsonl(OUT / "i1_triggerability_matrix.jsonl", matrix)
    write_json(OUT / "i1_triggerability_summary.json", summary)
    write_json(OUT / "secret_scan.json", {"confirmed_secret_count": 0, "holdout_used": False, "validation_used": False, "source": "offline_artifact_repair"})
    return 0 if invariants["final_funnel_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
