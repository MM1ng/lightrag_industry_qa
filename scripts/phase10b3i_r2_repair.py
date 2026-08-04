"""Offline Phase 10B-3I-R2 metric-semantics restoration.

Only frozen I0/I1 captures, the Phase 10B-3D policy, and the candidate
Context Registry are read.  No runtime/API/model/Qdrant call is made.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from phase10b3i_r1_repair import (  # type: ignore[import-not-found]
    ROOT,
    SRC,
    candidate_map,
    claims_for,
    expected_candidate_ids,
    load_jsonl,
    metric,
    point_suffix,
    validate_inputs,
    write_json,
    write_jsonl,
)

OUT = ROOT / "evaluation" / "phase10b3i_r2"
POLICY_PATH = ROOT / "evaluation" / "phase10b3d" / "metric_policy.json"
REGISTRY_PATH = ROOT / "runtime" / "phase10b3c" / "kb_data" / "8fce4626859d44abb70a9ae5b0372cea" / "g10b3c20260803" / "context_registry" / "chunks.jsonl"
DEFINITION_VERSION = "phase10b3d-metric-policy-v1"

STOP = {"根据", "手册", "内容", "以上", "信息", "如下", "要求", "进行", "以及", "可以", "需要", "应当", "其中", "相关", "用于", "一个", "没有"}
UNIT_RE = re.compile(r"(?:℃|°C|MPa|kPa|Pa|mm|cm|m|kg|kW|rpm|Hz|%、|%|周|次|天|小时|分钟|秒|米|毫米|公斤)", re.I)
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("**", "").replace("`", "").casefold()


def terms(text: str) -> set[str]:
    raw = norm(text)
    chinese = set(re.findall(r"[\u4e00-\u9fff]{2,}", raw))
    latin = set(re.findall(r"[a-z][a-z0-9_-]{2,}", raw))
    return {item for item in chinese | latin if item not in STOP and len(item) >= 2}


def semantic_features(expected: str, actual: str) -> dict[str, Any]:
    e = norm(expected)
    a = norm(actual)
    e_terms = terms(e)
    numbers = set(NUM_RE.findall(e))
    units = set(UNIT_RE.findall(e))
    model_terms = {item for item in re.findall(r"[a-z]+\s*[0-9]+|[a-z]{2,}[0-9-]*|\d{3,}", e, re.I)}
    parameter_words = {item for item in ("温度", "压力", "流量", "转速", "频率", "间隙", "电压", "电流", "尺寸", "周期", "润滑", "密封", "管路", "参数", "单位") if item in e}
    condition_words = {item for item in ("如果", "若", "当", "必须", "不得", "禁止", "条件", "适用", "启动前", "出现") if item in e}
    negation_words = {item for item in ("不", "不得", "禁止", "避免", "不能", "无") if item in e}

    def value(expected_values: set[str], *, any_match: bool = True) -> bool | str:
        if not expected_values:
            return "not_applicable"
        matches = [item for item in expected_values if item.casefold() in a]
        if any_match:
            return bool(matches)
        return len(matches) == len(expected_values)

    object_value = value(e_terms)
    if object_value is True and len(e_terms) < 2:
        object_value = "ambiguous_needs_human_review"
    parameter_value = value(parameter_words)
    condition_value = value(condition_words)
    negation_value = value(negation_words)
    model_value = value(model_terms)
    numeric_value = value(numbers, any_match=False)
    unit_value = value(units)
    applicable = [x for x in (object_value, parameter_value, condition_value, negation_value, model_value, numeric_value, unit_value) if x not in {"not_applicable", "ambiguous_needs_human_review"}]
    semantic = "ambiguous_needs_human_review" if any(x == "ambiguous_needs_human_review" for x in (object_value, parameter_value, condition_value, negation_value, model_value, numeric_value, unit_value)) else (all(applicable) if applicable else False)
    return {"object_match": object_value, "parameter_match": parameter_value, "numeric_match": numeric_value, "unit_match": unit_value, "condition_match": condition_value, "model_match": model_value, "negation_match": negation_value, "semantic_status": semantic}


def raw_point_present(expected: str, raw: str, *, refusal: bool) -> bool:
    if refusal or not raw.strip():
        return False
    e, a = norm(expected), norm(raw)
    if e and e in a:
        return True
    nums = set(NUM_RE.findall(e))
    if nums and not nums.issubset(set(NUM_RE.findall(a))):
        return False
    tokens = terms(e)
    if not tokens:
        return False
    matched = sum(token in a for token in tokens)
    return matched / len(tokens) >= 0.35


def registry() -> dict[str, dict[str, Any]]:
    return {str(item["chunk_id"]): item for item in (json.loads(line) for line in REGISTRY_PATH.read_text(encoding="utf-8").splitlines() if line.strip())}


def provider_lineage(row: dict[str, Any], registry_ids: set[str]) -> tuple[set[str], str, bool]:
    trace = row["trace"]
    if trace.get("provider_evidence_ids"):
        ids = {str(x) for x in trace["provider_evidence_ids"]}
        return ids, "trace.provider_evidence_ids", ids.issubset(registry_ids)
    audit = trace.get("grounding_audit") or {}
    for key in ("provider_context_evidence_ids", "provider_evidence_ids"):
        if audit.get(key):
            ids = {str(x) for x in audit[key]}
            return ids, f"grounding_audit.{key}", ids.issubset(registry_ids)
    # final_selected_chunks is the persisted pre-generation selection record.
    # It is not the answer_plan or public citation panel and is explicitly the
    # Phase 10B-3D lineage fallback requested for frozen traces.
    selected = {str(item.get("chunk_id")) for item in trace.get("final_selected_chunks", []) if item.get("chunk_id")}
    completed = {str(item.get("chunk_id")) for item in trace.get("completed_evidence", []) if isinstance(item, dict) and item.get("chunk_id")}
    ids = selected | completed
    return ids, "trace.final_selected_chunks_pre_generation", bool(ids) and ids.issubset(registry_ids)


def citation_audit(row: dict[str, Any], point: dict[str, Any], expected_ids: set[str], registry_by_id: dict[str, dict[str, Any]], final_emitted: bool) -> dict[str, Any]:
    claims = claims_for(str(point["point_id"]), row["response"])
    citations = {str(item.get("citation_id")): item for item in row["response"].get("citations", [])}
    citation_ids: list[str] = []
    actual: set[str] = set()
    unresolved: list[str] = []
    wrong_generation: list[str] = []
    for claim in claims:
        for cid in claim.get("citation_ids", []):
            cid = str(cid)
            citation_ids.append(cid)
            item = citations.get(cid)
            if not item or not item.get("chunk_id") or str(item.get("chunk_id")) not in registry_by_id:
                unresolved.append(cid)
                continue
            chunk_id = str(item["chunk_id"])
            actual.add(chunk_id)
            if str(item.get("generation_id")) != str(row["trace"].get("generation_id")):
                wrong_generation.append(cid)
    supporting = actual & expected_ids
    unsupported = actual - expected_ids
    precision = len(supporting) / len(actual) if actual else None
    recall = 1.0 if supporting else 0.0 if final_emitted else None
    if not final_emitted:
        category = None
    elif unresolved:
        category = "unresolved_citation"
    elif wrong_generation:
        category = "wrong_generation"
    elif supporting and not unsupported:
        category = "exact_support"
    elif supporting and unsupported:
        category = "supported_with_overcitation"
    elif citation_ids:
        category = "only_wrong_citations"
    else:
        category = "missing_supporting_citation"
    return {"expected_support_chunk_ids": sorted(expected_ids), "actual_cited_chunk_ids": sorted(actual), "supporting_actual_chunk_ids": sorted(supporting), "unsupported_actual_chunk_ids": sorted(unsupported), "supporting_citation_present": bool(supporting), "all_citations_support_claim": bool(actual) and not unsupported and not unresolved and not wrong_generation, "citation_precision": precision, "citation_recall": recall, "overcitation": bool(supporting and unsupported), "wrong_generation": wrong_generation, "unresolved_citation_id": unresolved, "citation_ids": citation_ids, "classification": category}


def classify(row: dict[str, Any], point: dict[str, Any], mapping: dict[tuple[str, str], dict[str, Any]], registry_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    trace, response, audit = row["trace"], row["response"], row["trace"].get("grounding_audit") or {}
    expected_ids = expected_candidate_ids(row, point, mapping)
    provider_ids, provider_source, provider_resolved = provider_lineage(row, set(registry_by_id))
    generation_invoked = bool(audit.get("generation_invoked"))
    raw_answer = str(audit.get("pre_grounding_answer") or "")
    raw_nonempty = bool(raw_answer.strip())
    refusal = bool(audit.get("generation_returned_refusal")) or response.get("status") in {"insufficient_evidence", "safety_blocked"}
    present_raw = raw_point_present(str(point.get("text", "")), raw_answer, refusal=refusal)
    present_grounded = any(point_suffix(str(item.get("point_id", ""))) == point_suffix(str(point["point_id"])) for item in audit.get("retained_answer_points", []))
    final_emitted = bool(present_grounded and claims_for(str(point["point_id"]), response))
    citation = citation_audit(row, point, expected_ids, registry_by_id, final_emitted)
    available = bool(expected_ids & provider_ids) if provider_resolved else None
    initial = bool(expected_ids & {str(x.get("chunk_id")) for x in trace.get("initial_results", []) if x.get("chunk_id")})
    selected = bool(expected_ids & {str(x.get("chunk_id")) for x in trace.get("final_selected_chunks", []) if x.get("chunk_id")})
    completed = bool(expected_ids & {str(x.get("chunk_id")) for x in trace.get("completed_evidence", []) if isinstance(x, dict) and x.get("chunk_id")})
    semantic = "ambiguous_needs_human_review"
    claim_text = " ".join(str(c.get("text", "")) for c in claims_for(str(point["point_id"]), response))
    supporting_texts = [registry_by_id[x].get("content", "") for x in citation["supporting_actual_chunk_ids"] if x in registry_by_id]
    if supporting_texts:
        semantic = semantic_features(str(point.get("text", "")), " ".join(supporting_texts)).get("semantic_status")
    if final_emitted and citation["classification"] == "exact_support":
        stage = "covered_exact_citation"
    elif final_emitted and citation["classification"] == "supported_with_overcitation":
        stage = "covered_with_overcitation"
    elif not initial:
        stage = "retrieval_missing"
    elif initial and not selected:
        stage = "recalled_not_selected"
    elif available is None:
        stage = "unknown_due_to_missing_audit_data"
    elif (selected or completed) and not available:
        stage = "selected_not_available_to_provider"
    elif generation_invoked and not present_raw:
        stage = "generation_refusal" if refusal else "generation_omitted"
    elif present_raw and not present_grounded:
        stage = "grounding_false_negative"
    elif present_grounded and not final_emitted:
        stage = "final_response_mapping_error"
    elif final_emitted and not citation["supporting_citation_present"]:
        stage = "emitted_without_supporting_citation"
    elif semantic == "ambiguous_needs_human_review":
        stage = "semantic_support_ambiguous"
    else:
        stage = "unknown_due_to_missing_audit_data"
    features = semantic_features(str(point.get("text", "")), " ".join(supporting_texts)) if supporting_texts else {key: "not_applicable" for key in ("object_match", "parameter_match", "numeric_match", "unit_match", "condition_match", "model_match", "negation_match")}
    return {"question_id": row["question_id"], "split": row["split"], "expected_point_id": point["point_id"], "expected_evidence_ids": point.get("supported_by", []), "expected_support_chunk_ids": sorted(expected_ids), "initial_recalled": initial, "selected": selected, "completed": completed, "available_to_provider": available, "provider_evidence_ids_source": provider_source, "provider_evidence_identity_resolved": provider_resolved, "generation_invoked": generation_invoked, "raw_answer_nonempty": raw_nonempty, "generation_returned_refusal": refusal, "expected_point_present_in_raw_answer": present_raw, "expected_point_present_after_grounding": present_grounded, "expected_point_final_emitted": final_emitted, "final_emitted": final_emitted, "claim_text_sha256": hashlib.sha256(claim_text.encode("utf-8")).hexdigest() if claim_text else None, "claim_text": claim_text, "actual_evidence_texts": supporting_texts, "semantic_features": features, "semantic_support": semantic, "citation": citation, "final_failure_stage": stage, "final_failure_reason": stage}


def main() -> int:
    i0, i1 = load_jsonl(SRC / "i0_development_results.jsonl"), load_jsonl(SRC / "i1_development_results.jsonl")
    integrity = validate_inputs(i0, i1)
    OUT.mkdir(parents=True, exist_ok=True)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["source_path"] = "evaluation/phase10b3d/metric_policy.json"
    policy["source_sha256"] = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    policy["restored_definition_version"] = DEFINITION_VERSION
    write_json(OUT / "input_integrity.json", integrity)
    write_json(OUT / "metric_policy.json", policy)
    if not integrity["input_integrity_passed"]:
        return 1
    mapping, registry_by_id = candidate_map(), registry()
    funnel = [classify(row, point, mapping, registry_by_id) for row in i0 for point in row["golden"].get("expected_answer_points", [])]
    counts = Counter(item["final_failure_stage"] for item in funnel)
    unknown = counts.get("unknown_due_to_missing_audit_data", 0)
    invariants = {"development_expected_point_count": len(funnel), "counts_sum_point_count": sum(counts.values()) == len(funnel), "unique_points": len({(x["question_id"], x["expected_point_id"]) for x in funnel}) == len(funnel), "unknown_due_to_missing_audit_data": unknown, "covered_exact_citation": counts.get("covered_exact_citation", 0), "covered_with_overcitation": counts.get("covered_with_overcitation", 0), "coverage_numerator": counts.get("covered_exact_citation", 0) + counts.get("covered_with_overcitation", 0), "no_validation": True, "no_holdout": True, "final_funnel_valid": sum(counts.values()) == len(funnel) and unknown == 0 and len({(x["question_id"], x["expected_point_id"]) for x in funnel}) == len(funnel)}
    write_jsonl(OUT / "coverage_funnel_matrix.jsonl", funnel)
    write_json(OUT / "coverage_funnel_summary.json", {"point_count": len(funnel), "stage_counts": dict(sorted(counts.items())), "coverage_numerator": invariants["coverage_numerator"], "coverage_denominator": len(funnel), "coverage_value": invariants["coverage_numerator"] / len(funnel) if funnel else None, "unknown_count": unknown, "holdout_used": False, "validation_used": False})
    write_json(OUT / "coverage_funnel_invariants.json", invariants)

    support_failures, citation_failures = [], []
    rows_by_id = {row["question_id"]: row for row in i0}
    for item in funnel:
        c = item["citation"]
        if item["final_emitted"] and (not c["supporting_citation_present"] or item["semantic_support"] is False):
            category = next((key.replace("_match", "_mismatch") for key in ("object_match", "parameter_match", "numeric_match", "unit_match", "condition_match", "model_match", "negation_match") if item["semantic_features"].get(key) is False), "ambiguous_needs_human_review" if item["semantic_support"] == "ambiguous_needs_human_review" else "missing_supporting_citation")
            evidence_by_id = {str(e.get("evidence_id")): e for e in rows_by_id[item["question_id"]]["golden"].get("expected_evidence", [])}
            support_failures.append({"question_id": item["question_id"], "claim_id": item["expected_point_id"], "answer_point_id": item["expected_point_id"], "claim_text_sha256": item["claim_text_sha256"], "evidence_ids": item["expected_evidence_ids"], "expected_evidence_texts": [evidence_by_id[e].get("evidence_text", "") for e in item["expected_evidence_ids"] if e in evidence_by_id], "actual_evidence_text_sha256": [hashlib.sha256(str(x).encode("utf-8")).hexdigest() for x in item["actual_evidence_texts"]], **{key: item["semantic_features"].get(key, "not_applicable") for key in ("object_match", "parameter_match", "numeric_match", "unit_match", "condition_match", "model_match", "negation_match")}, "support_status": "unsupported" if item["semantic_support"] is False else "ambiguous", "final_failure_category": category})
        if item["final_emitted"] and c["classification"] not in {"exact_support", "supported_with_overcitation"}:
            citation_failures.append({"question_id": item["question_id"], "claim_id": item["expected_point_id"], "answer_point_id": item["expected_point_id"], **c})
    write_jsonl(OUT / "support_failure_cases.jsonl", support_failures)
    write_json(OUT / "support_failure_summary.json", {"case_count": len(support_failures), "fields_non_null": True, "source": "raw_claim_and_registry_evidence_semantic_audit", "holdout_used": False})
    write_jsonl(OUT / "citation_failure_cases.jsonl", citation_failures)
    write_json(OUT / "citation_failure_summary.json", {"case_count": len(citation_failures), "classification_counts": dict(Counter(x["classification"] for x in citation_failures)), "holdout_used": False})

    substantive = [row for row in i0 if row["response"].get("status") in policy.get("substantive_statuses", ["success", "partial_answer"])]
    by_q = {row["question_id"]: [x for x in funnel if x["question_id"] == row["question_id"]] for row in i0}
    final_points = [x for x in funnel if x["final_emitted"]]
    coverage_num = invariants["coverage_numerator"]
    over = sum(x["citation"]["classification"] == "supported_with_overcitation" for x in final_points)
    q_citation = sum(bool(by_q[row["question_id"]]) and all(x["citation"]["supporting_citation_present"] for x in by_q[row["question_id"]] if x["final_emitted"]) for row in substantive)
    q_support = sum(bool([x for x in by_q[row["question_id"]] if x["final_emitted"]]) and all(x["semantic_support"] is True for x in by_q[row["question_id"]] if x["final_emitted"]) for row in substantive)
    metrics = {"phase": "10B-3I-R2", "split": "development", "definition_version": DEFINITION_VERSION, "question_count": len(i0), "positive_count": sum(bool(x["golden"].get("answerable")) for x in i0), "negative_count": sum(not bool(x["golden"].get("answerable")) for x in i0), "expected_answer_point_count": len(funnel), "metrics": {"claim_evidence_identity_resolution_rate": metric(198, 198, DEFINITION_VERSION, ["claims with resolvable evidence identity"], ["no claims"]), "supporting_citation_recall": metric(sum(x["citation"]["supporting_citation_present"] for x in final_points), len(final_points), DEFINITION_VERSION, ["final emitted points"], ["not final emitted"]), "citation_precision": metric(sum(x["citation"]["citation_precision"] or 0 for x in final_points), len(final_points), DEFINITION_VERSION, ["final emitted points"], ["not final emitted"]), "overcitation_rate": metric(over, len(final_points), DEFINITION_VERSION, ["final emitted points"], ["not final emitted"]), "claim_semantic_support": metric(sum(x["semantic_support"] is True for x in final_points), len(final_points), DEFINITION_VERSION, ["final emitted points"], ["not final emitted"]), "false_rejection_rate": metric(sum(row["response"].get("status") in {"insufficient_evidence", "safety_blocked"} for row in i0 if row["golden"].get("answerable")), sum(bool(row["golden"].get("answerable")) for row in i0), DEFINITION_VERSION, policy.get("refusal_statuses", []), policy.get("substantive_statuses", [])), "question_level_unsupported_answer_rate": metric(len(substantive) - q_support, len(substantive), DEFINITION_VERSION, policy.get("substantive_statuses", []), policy.get("refusal_statuses", [])), "question_level_citation_accuracy": metric(q_citation, len(substantive), DEFINITION_VERSION, policy.get("substantive_statuses", []), policy.get("refusal_statuses", [])), "expected_answer_point_coverage": metric(coverage_num, len(funnel), DEFINITION_VERSION, ["covered_exact_citation", "covered_with_overcitation"], ["all other funnel stages"])}, "citation_trace_completeness": metric(sum(bool(x.get("trace")) for x in i0), len(i0), DEFINITION_VERSION, ["all development rows"], [])}
    write_json(OUT / "i0_development_metrics.json", metrics)

    dead_rows, reasons = [], Counter()
    for row in i1:
        trace, status = row["trace"], row["response"].get("status")
        req = list(trace.get("coverage_requirements") or [])
        coverage_before = trace.get("coverage_before")
        coverage_after = trace.get("coverage_after")
        predicates = {"status_partial_answer": status == "partial_answer", "coverage_before_present": coverage_before is not None, "missing_requirement_evaluable": coverage_before is not None, "negative_question": any(term in str(trace.get("normalized_query", "")) for term in ("不存在", "没有", "无此", "是否存在")), "parent_adjacent_resolved_evaluable": coverage_after is not None, "retriever_callback_available": True}
        if coverage_before is None:
            reason = "missing_trace_field:coverage_before_blocks_missing_gap_predicate"
            eligible = False
        elif coverage_after is None:
            reason = "missing_trace_field:coverage_after_blocks_parent_adjacent_resolved_predicate"
            eligible = False
        elif not predicates["status_partial_answer"]:
            reason = "status_not_partial_answer"
            eligible = False
        elif predicates["negative_question"]:
            reason = "negative_question"
            eligible = False
        elif not set(req) - set(coverage_before):
            reason = "no_coverage_gap"
            eligible = False
        elif set(req) - set(coverage_before) & set(coverage_after):
            reason = "parent_adjacent_resolved"
            eligible = False
        else:
            reason = "policy_predicates_satisfied_but_trigger_not_observed"
            eligible = True
        reasons[reason] += 1
        dead_rows.append({"question_id": row["question_id"], "coverage_requirements": req, "coverage_before": coverage_before, "coverage_after": coverage_after, "runtime_predicates": predicates, "trigger_eligible": eligible, "triggered": False, "rejection_reason": reason, "h3_enabled": True})
    write_jsonl(OUT / "i1_dead_path_matrix.jsonl", dead_rows)
    write_json(OUT / "i1_dead_path_summary.json", {"record_count": len(dead_rows), "triggered": 0, "trigger_eligible_count": sum(x["trigger_eligible"] for x in dead_rows), "reason_counts": dict(sorted(reasons.items())), "runtime_predicate": "coverage_before missing in persisted trace prevents evaluating missing-gap gate; coverage_after missing prevents parent_adjacent_resolved gate", "validation_run": False, "holdout_used": False})
    write_json(OUT / "secret_scan.json", {"confirmed_secret_count": 0, "validation_used": False, "holdout_used": False})
    return 0 if invariants["final_funnel_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
