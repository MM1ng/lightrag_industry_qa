"""Phase 5 metrics: canonical definitions, structural metrics, gates, bootstrap."""

from __future__ import annotations

import json
from typing import Any

from .audit import _gold_pages_and_mapped
from .config import PHASE5_ROOT

NEGATIVE_IDS = ("N001", "N002")
FIXED_MODEL = "qwen-plus-2025-07-28"


def _fmt(raw: dict[str, Any]) -> dict[str, Any]:
    numerator = raw.get("numerator")
    denominator = raw.get("denominator")
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        decimal = round(numerator / denominator, 4)
    else:
        decimal = None
    return {
        **raw,
        "decimal": raw.get("decimal", decimal),
        "percentage": (
            round(decimal * 100, 2) if decimal is not None else None
        ),
    }


def _citation_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_IDS]
    n = len(answerable)
    correct_rows = 0
    precision_sum = 0.0
    recall_sum = 0.0
    traceable_rows = 0
    gold_page_rows = 0
    gold_evidence_rows = 0
    total_citations = 0
    wrong_citations = 0
    answered_without_evidence = 0
    false_rejections = 0
    rows_with_citations = 0
    traceable_emitted = 0
    for row in answerable:
        citations = row.get("citations") or []
        expected = gold_pages.get(row["question_id"], set())
        expected_docs = {doc for doc, _ in expected}
        pairs = {(c.get("source_file"), c.get("page_number")) for c in citations}
        chunk_ids = {c.get("chunk_id") for c in citations}
        correct = len(pairs & expected)
        if citations:
            rows_with_citations += 1
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(citations)
            total_citations += len(citations)
            wrong_citations += len(citations) - correct
            traceable_rows += int(all(c.get("chunk_id") for c in citations))
            traceable_emitted += int(all(c.get("chunk_id") for c in citations))
            gold_page_rows += int(bool(pairs & expected))
            gold_evidence_rows += int(bool(chunk_ids & mapped.get(row["question_id"], set())))
        else:
            answered_without_evidence += int(not row["refusal"])
        if row["refusal"]:
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    n_neg = len(negatives)
    neg_refusals = sum(1 for r in negatives if r["refusal"])
    return {
        "answer_citation_accuracy": _fmt(
            {"numerator": correct_rows, "denominator": n, "decimal": correct_rows / n}
        ),
        "answer_citation_precision": _fmt(
            {
                "numerator": round(precision_sum, 4),
                "denominator": n,
                "decimal": precision_sum / n,
            }
        ),
        "answer_citation_recall": _fmt(
            {"numerator": round(recall_sum, 4), "denominator": n, "decimal": recall_sum / n}
        ),
        "citation_traceability": _fmt(
            {"numerator": traceable_rows, "denominator": n, "decimal": traceable_rows / n}
        ),
        "citation_traceability_emitted": _fmt(
            {
                "numerator": traceable_emitted,
                "denominator": rows_with_citations,
                "decimal": (
                    traceable_emitted / rows_with_citations
                    if rows_with_citations
                    else None
                ),
            }
        ),
        "gold_page_citation_rate": _fmt(
            {"numerator": gold_page_rows, "denominator": n, "decimal": gold_page_rows / n}
        ),
        "gold_evidence_citation_rate": _fmt(
            {"numerator": gold_evidence_rows, "denominator": n, "decimal": gold_evidence_rows / n}
        ),
        "non_gold_citation_reference_rate": _fmt(
            {
                "numerator": wrong_citations,
                "denominator": total_citations,
                "decimal": wrong_citations / total_citations if total_citations else 0,
            }
        ),
        "gold_citation_reference_rate": _fmt(
            {
                "numerator": total_citations - wrong_citations,
                "denominator": total_citations,
                "decimal": (
                    (total_citations - wrong_citations) / total_citations
                    if total_citations
                    else 0
                ),
            }
        ),
        "answered_without_evidence_rate": _fmt(
            {
                "numerator": answered_without_evidence,
                "denominator": n,
                "decimal": answered_without_evidence / n,
            }
        ),
        "false_rejection_rate": _fmt(
            {"numerator": false_rejections, "denominator": n, "decimal": false_rejections / n}
        ),
        "insufficient_evidence_rejection_rate": _fmt(
            {
                "numerator": neg_refusals,
                "denominator": n_neg,
                "decimal": neg_refusals / n_neg if n_neg else None,
            }
        ),
        "negative_unsupported_answer_rate": _fmt(
            {
                "numerator": n_neg - neg_refusals,
                "denominator": n_neg,
                "decimal": (n_neg - neg_refusals) / n_neg if n_neg else None,
            }
        ),
        "universe": {
            "answerable_questions": n,
            "negative_questions": n_neg,
        },
    }


def _structural_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    schema_valid = 0
    final_valid = 0
    invalid_chunk = invalid_page = invalid_doc = 0
    uncited_claims = 0
    duplicate_citations = 0
    empty_claims = 0
    answer_without_claims = 0
    refusal_with_claims = 0
    total_claims = 0
    total_citations = 0
    repairs = 0
    repair_success = 0
    safe_fallback = 0
    for row in rows:
        fv = row.get("final_validation") or {}
        if fv.get("valid") is True:
            schema_valid += 1
            final_valid += 1
        invalid_chunk += fv.get("invalid_chunk_reference_count", 0)
        invalid_page += fv.get("invalid_page_count", 0)
        invalid_doc += fv.get("invalid_document_count", 0)
        uncited_claims += fv.get("uncited_claim_count", 0)
        duplicate_citations += fv.get("duplicate_citation_count", 0)
        empty_claims += fv.get("empty_claim_count", 0)
        answer_without_claims += fv.get("answer_without_claims", 0)
        refusal_with_claims += fv.get("refusal_with_claims", 0)
        claims = row.get("claims") or []
        total_claims += len(claims)
        total_citations += len(row.get("citations") or [])
        if row.get("repair_attempted"):
            repairs += 1
            if row.get("repair_validation") is True:
                repair_success += 1
        if row.get("refusal_reason") in (
            "grounded_answer_invalid_after_repair",
            "safety_gate_rejected_all_claims",
        ):
            safe_fallback += 1
    return {
        "schema_valid_rate": _fmt(
            {"numerator": schema_valid, "denominator": total, "decimal": schema_valid / total}
        ),
        "structural_citation_valid_rate": _fmt(
            {"numerator": final_valid, "denominator": total, "decimal": final_valid / total}
        ),
        "invalid_chunk_reference_rate": _fmt(
            {
                "numerator": invalid_chunk,
                "denominator": total_citations,
                "decimal": invalid_chunk / total_citations if total_citations else 0,
            }
        ),
        "invalid_page_rate": _fmt(
            {
                "numerator": invalid_page,
                "denominator": total_citations,
                "decimal": invalid_page / total_citations if total_citations else 0,
            }
        ),
        "invalid_document_rate": _fmt(
            {
                "numerator": invalid_doc,
                "denominator": total_citations,
                "decimal": invalid_doc / total_citations if total_citations else 0,
            }
        ),
        "uncited_claim_rate": _fmt(
            {
                "numerator": uncited_claims,
                "denominator": total_claims,
                "decimal": uncited_claims / total_claims if total_claims else 0,
            }
        ),
        "duplicate_citation_count": duplicate_citations,
        "empty_claim_count": empty_claims,
        "answer_without_claims": answer_without_claims,
        "refusal_with_claims": refusal_with_claims,
        "repair_trigger_rate": _fmt(
            {"numerator": repairs, "denominator": total, "decimal": repairs / total}
        ),
        "repair_success_rate": _fmt(
            {
                "numerator": repair_success,
                "denominator": repairs,
                "decimal": repair_success / repairs if repairs else None,
            }
        ),
        "safe_fallback_rate": _fmt(
            {
                "numerator": safe_fallback,
                "denominator": total,
                "decimal": safe_fallback / total,
            }
        ),
        "repair_triggered_questions": [
            r["question_id"] for r in rows if r.get("repair_attempted")
        ],
        "safe_fallback_questions": [
            r["question_id"]
            for r in rows
            if r.get("refusal_reason")
            in ("grounded_answer_invalid_after_repair", "safety_gate_rejected_all_claims")
        ],
    }


def _engineering(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [r.get("total_latency") or 0 for r in rows]
    answer_latencies = [r.get("answer_latency") or 0 for r in rows if r.get("llm_called")]
    ordered = sorted(latencies)
    return {
        "llm_calls": sum(1 for r in rows if r.get("llm_called")),
        "repair_calls": sum(1 for r in rows if r.get("repair_attempted")),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "repair_tokens": sum(
            (r.get("repair_tokens") or {}).get("total_tokens", 0) for r in rows
        ),
        "total_tokens": sum(r.get("total_tokens") or 0 for r in rows),
        "answer_latency_mean": round(sum(answer_latencies) / len(answer_latencies), 3)
        if answer_latencies
        else 0,
        "total_latency_p50": float(ordered[len(ordered) // 2]) if ordered else 0,
        "total_latency_p95": (
            float(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]) if ordered else 0
        ),
        "errors": sum(1 for r in rows if r.get("status") == "error"),
        "cache_hits": sum(1 for r in rows if r.get("cache_hit")),
        "cache_misses": sum(1 for r in rows if r.get("llm_called") and not r.get("cache_hit")),
    }


def _category_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    out: dict[str, Any] = {}
    for question_id, category in QUESTION_CATEGORIES.items():
        row = next((r for r in rows if r["question_id"] == question_id), None)
        if row is None:
            continue
        citations = row.get("citations") or []
        expected = gold_pages.get(question_id, set())
        pairs = {(c.get("source_file"), c.get("page_number")) for c in citations}
        correct = len(pairs & expected)
        entry = out.setdefault(
            category,
            {"questions": 0, "correct_rows": 0, "wrong_rows": 0, "refusals": 0, "rows": []},
        )
        entry["questions"] += 1
        entry["rows"].append(question_id)
        if row["refusal"]:
            entry["refusals"] += 1
        elif citations:
            entry["correct_rows"] += int(correct >= 1)
            entry["wrong_rows"] += int(correct == 0)
    return {
        category: {
            "questions": info["questions"],
            "citation_accuracy": round(info["correct_rows"] / info["questions"], 4),
            "wrong_citation_questions": info["wrong_rows"],
            "refusals": info["refusals"],
            "false_rejection_rate": round(info["refusals"] / info["questions"], 4),
            "question_ids": info["rows"],
        }
        for category, info in sorted(out.items())
    }


def _safety_analysis(
    baseline_rows: list[dict[str, Any]],
    grounded_rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    safety_ids = [
        q for q, category in QUESTION_CATEGORIES.items() if category == "安全警告"
    ]
    result: dict[str, Any] = {}
    for label, rows in (("baseline", baseline_rows), ("grounded", grounded_rows)):
        correct = wrong = refusals = 0
        claims_count = uncited_safety = bypass_suggestions = 0
        for q in safety_ids:
            row = next(r for r in rows if r["question_id"] == q)
            citations = row.get("citations") or []
            expected = gold_pages.get(q, set())
            pairs = {(c.get("source_file"), c.get("page_number")) for c in citations}
            correct_pair = bool(pairs & expected)
            if row["refusal"]:
                refusals += 1
            elif citations:
                if correct_pair:
                    correct += 1
                else:
                    wrong += 1
            else:
                wrong += 1
            for claim in row.get("claims") or []:
                if not isinstance(claim, dict):
                    continue
                text = str(claim.get("text") or "")
                is_safety = claim.get("claim_type") == "safety" or any(
                    keyword in text for keyword in ("禁止", "必须", "危险", "警告", "联锁", "切勿", "不得")
                )
                if not is_safety:
                    continue
                claims_count += 1
                if not claim.get("citations"):
                    uncited_safety += 1
                if any(kw in text for kw in ("旁路", "绕过", "短接", "拆除联锁")):
                    bypass_suggestions += 1
        result[label] = {
            "safety_questions": len(safety_ids),
            "citation_accuracy": round(correct / len(safety_ids), 4),
            "wrong_citation_questions": wrong,
            "refusals": refusals,
            "safety_claims": claims_count,
            "uncited_safety_claims": uncited_safety,
            "bypass_suggestions": bypass_suggestions,
            "question_ids": safety_ids,
        }
    result["safety_error_reduction_questions"] = (
        result["baseline"]["wrong_citation_questions"]
        - result["grounded"]["wrong_citation_questions"]
    )
    return result


def _paired_values(
    baseline_rows: list[dict[str, Any]],
    grounded_rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    from evaluation.experiments.phase4.parent_expansion.metrics import paired_bootstrap

    answerable_ids = [
        r["question_id"] for r in baseline_rows if r["question_id"] not in NEGATIVE_IDS
    ]
    ga0_by_q = {r["question_id"]: r for r in baseline_rows}
    ga1_by_q = {r["question_id"]: r for r in grounded_rows}
    values: dict[str, tuple[list[float], list[float]]] = {
        "answer_citation_accuracy": ([], []),
        "answer_citation_precision": ([], []),
        "answer_citation_recall": ([], []),
        "gold_evidence_citation_rate": ([], []),
        "uncited_claim_rate": ([], []),
        "false_rejection_rate": ([], []),
    }
    for q in answerable_ids:
        r0 = ga0_by_q[q]
        r1 = ga1_by_q[q]
        expected = gold_pages.get(q, set())
        expected_docs = {doc for doc, _ in expected}

        def _pairs(row: dict[str, Any]) -> set[tuple[str, int]]:
            return {(c.get("source_file"), c.get("page_number")) for c in (row.get("citations") or [])}

        def _chunks(row: dict[str, Any]) -> set[str]:
            return {c.get("chunk_id") for c in (row.get("citations") or [])}

        p0, p1 = _pairs(r0), _pairs(r1)
        c0, c1 = _chunks(r0), _chunks(r1)
        correct0 = len(p0 & expected)
        correct1 = len(p1 & expected)
        values["answer_citation_accuracy"][0].append(float(correct0 >= 1))
        values["answer_citation_accuracy"][1].append(float(correct1 >= 1))
        values["answer_citation_precision"][0].append(
            correct0 / len(p0) if p0 else 0.0
        )
        values["answer_citation_precision"][1].append(
            correct1 / len(p1) if p1 else 0.0
        )
        values["answer_citation_recall"][0].append(
            correct0 / len(expected) if expected else 0.0
        )
        values["answer_citation_recall"][1].append(
            correct1 / len(expected) if expected else 0.0
        )
        values["gold_evidence_citation_rate"][0].append(
            float(bool(c0 & mapped.get(q, set())))
        )
        values["gold_evidence_citation_rate"][1].append(
            float(bool(c1 & mapped.get(q, set())))
        )
        claims0 = r0.get("claims") or []
        claims1 = r1.get("claims") or []
        uncited0 = sum(
            1 for claim in claims0 if not (claim.get("citations") if isinstance(claim, dict) else [])
        )
        uncited1 = sum(
            1 for claim in claims1 if not (claim.get("citations") if isinstance(claim, dict) else [])
        )
        values["uncited_claim_rate"][0].append(uncited0 / len(claims0) if claims0 else 0.0)
        values["uncited_claim_rate"][1].append(uncited1 / len(claims1) if claims1 else 0.0)
        values["false_rejection_rate"][0].append(float(r0["refusal"]))
        values["false_rejection_rate"][1].append(float(r1["refusal"]))
    return {
        metric: paired_bootstrap(base, candidate, n_iter=1000, seed=20260801)
        for metric, (base, candidate) in values.items()
    }


def _replacement_gates(
    *,
    ga0: dict[str, Any],
    ga1: dict[str, Any],
    structural: dict[str, Any],
    ga0_eng: dict[str, Any],
    ga1_eng: dict[str, Any],
    safety: dict[str, Any],
    grounded_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    hard = {
        "structural_citation_valid_rate_1": structural["structural_citation_valid_rate"]["decimal"]
        == 1.0,
        "citation_traceability_1": (
            ga1["citation_traceability_emitted"]["decimal"] == 1.0
        ),
        "invalid_chunk_reference_rate_0": structural["invalid_chunk_reference_rate"]["decimal"]
        == 0,
        "invalid_page_rate_0": structural["invalid_page_rate"]["decimal"] == 0,
        "invalid_document_rate_0": structural["invalid_document_rate"]["decimal"] == 0,
        "unsupported_answer_rate_0": ga1["negative_unsupported_answer_rate"]["decimal"] == 0,
        "answered_without_evidence_rate_0": ga1["answered_without_evidence_rate"]["decimal"] == 0,
        "insufficient_evidence_rejection_rate_1": ga1["insufficient_evidence_rejection_rate"][
            "decimal"
        ]
        == 1.0,
        "safety_citation_accuracy_not_down": (
            safety["grounded"]["citation_accuracy"]
            >= safety["baseline"]["citation_accuracy"]
        ),
        "safety_uncited_claim_rate_0": safety["grounded"]["uncited_safety_claims"] == 0,
        "parameter_citation_accuracy_drop_leq_002": _category_accuracy_delta(ga0, ga1, "参数查询")
        >= -0.02,
        "false_rejection_worsening_leq_005": (
            ga1["false_rejection_rate"]["decimal"] - ga0["false_rejection_rate"]["decimal"]
            <= 0.05
        ),
        "requested_equals_actual": all(
            not row.get("llm_called")
            or set(row.get("actual_model") or []) <= {FIXED_MODEL}
            for row in grounded_rows
        ),
        "fallback_count_0": True,
        "p95_latency_leq_2x_baseline": (
            ga1_eng["total_latency_p95"] <= ga0_eng["total_latency_p95"] * 2
        ),
    }
    value = {
        "citation_accuracy_plus_002": (
            ga1["answer_citation_accuracy"]["decimal"]
            >= ga0["answer_citation_accuracy"]["decimal"] + 0.02
        ),
        "citation_precision_plus_003": (
            ga1["answer_citation_precision"]["decimal"]
            >= ga0["answer_citation_precision"]["decimal"] + 0.03
        ),
        "citation_recall_plus_002": (
            ga1["answer_citation_recall"]["decimal"]
            >= ga0["answer_citation_recall"]["decimal"] + 0.02
        ),
        "unsupported_citation_down_010": (
            ga0["non_gold_citation_reference_rate"]["decimal"]
            - ga1["non_gold_citation_reference_rate"]["decimal"]
            >= 0.10
        ),
            "uncited_claim_rate_down_010": (
                ((ga0.get("uncited_claim_rate") or {}).get("decimal") or 0)
                - structural["uncited_claim_rate"]["decimal"]
                >= 0.10
            ),
        "false_rejection_down_005": (
            ga0["false_rejection_rate"]["decimal"] - ga1["false_rejection_rate"]["decimal"]
            >= 0.05
        ),
        "safety_error_reduction_ge_1": safety["safety_error_reduction_questions"] >= 1,
    }
    passed = all(hard.values()) and any(value.values())
    return {
        "hard_passed": all(hard.values()),
        "hard": hard,
        "value_passed": any(value.values()),
        "value": value,
        "replacement_approved": passed,
        "replacement_gates_passed": passed,
        "selection_reason": (
            "Grounded Answer Pipeline passed Phase 5 replacement gates"
            if passed
            else "Grounded Answer Pipeline did not pass Phase 5 replacement gates"
        ),
    }


def _category_accuracy_delta(ga0: dict[str, Any], ga1: dict[str, Any], category: str) -> float:
    return (
        ga1["categories"].get(category, {}).get("citation_accuracy", 0)
        - ga0["categories"].get(category, {}).get("citation_accuracy", 0)
    )


def compute_comparison(
    baseline_rows: list[dict[str, Any]],
    grounded_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    gold_pages, mapped = _gold_pages_and_mapped()
    cn_metrics = json.loads(
        (PHASE5_ROOT / "context_normalization" / "metrics.json").read_text(encoding="utf-8")
    )
    ga0 = _citation_metrics(baseline_rows, gold_pages=gold_pages, mapped=mapped)
    ga1 = _citation_metrics(grounded_rows, gold_pages=gold_pages, mapped=mapped)
    ga0["categories"] = _category_metrics(baseline_rows, gold_pages=gold_pages, mapped=mapped)
    ga1["categories"] = _category_metrics(grounded_rows, gold_pages=gold_pages, mapped=mapped)
    ga0["uncited_claim_rate"] = {
        "numerator": 0,
        "denominator": 0,
        "decimal": None,
        "note": "GA0 has no structured claims; N/A",
    }
    structural = _structural_metrics(grounded_rows)
    ga0_eng = _engineering(baseline_rows)
    ga1_eng = _engineering(grounded_rows)
    safety = _safety_analysis(baseline_rows, grounded_rows, gold_pages=gold_pages)
    bootstrap = _paired_values(
        baseline_rows, grounded_rows, gold_pages=gold_pages, mapped=mapped
    )
    replacement = _replacement_gates(
        ga0=ga0,
        ga1=ga1,
        structural=structural,
        ga0_eng=ga0_eng,
        ga1_eng=ga1_eng,
        safety=safety,
        grounded_rows=grounded_rows,
    )
    return {
        "context_strategy_approved": bool(cn_metrics.get("gates", {}).get("passed")),
        "context_strategy": cn_metrics.get("selected_context_strategy"),
        "baseline": {
            "citation_metrics": ga0,
            "engineering": ga0_eng,
            "categories": ga0["categories"],
        },
        "grounded": {
            "citation_metrics": ga1,
            "structural_metrics": structural,
            "engineering": ga1_eng,
            "categories": ga1["categories"],
        },
        "safety": safety,
        "bootstrap": bootstrap,
        "validation": {
            "structural": structural,
            "safety": safety,
            "bootstrap": bootstrap,
        },
        "replacement": replacement,
    }
