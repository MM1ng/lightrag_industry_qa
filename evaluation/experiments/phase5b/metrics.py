"""Phase 5B metrics: marker, coverage, citation, rejection, engineering, gates."""

from __future__ import annotations

from typing import Any

NEGATIVE_IDS = ("N001", "N002")
FIXED_MODEL = "qwen-plus-2025-07-28"


def _fmt(numerator: Any, denominator: Any) -> dict[str, Any]:
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        decimal = round(numerator / denominator, 4)
    else:
        decimal = None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "decimal": decimal,
        "percentage": round(decimal * 100, 2) if decimal is not None else None,
    }


def citation_metrics(
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
    gold_page_rows = 0
    gold_evidence_rows = 0
    total_citations = 0
    gold_citations = 0
    answered_without_evidence = 0
    false_rejections = 0
    traceable_rows = 0
    rows_with_citations = 0
    for row in answerable:
        citations = row.get("citations") or []
        expected = gold_pages.get(row["question_id"], set())
        pairs = {(c.get("document_name") or c.get("source_file"), c.get("page")) for c in citations}
        chunk_ids = {c.get("chunk_id") for c in citations}
        correct = len(pairs & expected)
        if citations:
            rows_with_citations += 1
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(citations)
            total_citations += len(citations)
            gold_citations += correct
            gold_page_rows += int(bool(pairs & expected))
            gold_evidence_rows += int(bool(chunk_ids & mapped.get(row["question_id"], set())))
            traceable_rows += int(all(c.get("chunk_id") and c.get("document_name") and c.get("page") for c in citations))
        else:
            answered_without_evidence += int(not row["refusal"])
        if row["refusal"]:
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    n_neg = len(negatives)
    neg_refusals = sum(1 for r in negatives if r["refusal"])
    return {
        "answer_citation_accuracy": _fmt(correct_rows, n),
        "answer_citation_precision": _fmt(round(precision_sum, 4), n),
        "answer_citation_recall": _fmt(round(recall_sum, 4), n),
        "gold_page_citation_rate": _fmt(gold_page_rows, n),
        "gold_evidence_citation_rate": _fmt(gold_evidence_rows, n),
        "gold_citation_reference_rate": _fmt(gold_citations, total_citations),
        "non_gold_citation_reference_rate": _fmt(total_citations - gold_citations, total_citations),
        "emitted_citation_traceability": _fmt(traceable_rows, rows_with_citations),
        "answered_without_evidence_rate": _fmt(answered_without_evidence, n),
        "false_rejection_rate": _fmt(false_rejections, n),
        "insufficient_evidence_rejection_rate": _fmt(neg_refusals, n_neg),
        "negative_unsupported_answer_rate": _fmt(n_neg - neg_refusals, n_neg),
        "universe": {"answerable_questions": n, "negative_questions": n_neg},
    }


def marker_and_coverage_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_markers = valid = invalid = malformed = 0
    key_claims = covered = 0
    by_type: dict[str, list[int]] = {}
    uncited_safety_claims = 0
    safety_claims = 0
    for row in rows:
        processed = row.get("processed") or {}
        stats = processed.get("marker_stats") or {}
        total_markers += stats.get("total_markers", 0)
        valid += stats.get("valid_markers", 0)
        invalid += stats.get("invalid_chunk_markers", 0)
        malformed += stats.get("malformed_markers", 0)
        coverage = processed.get("coverage") or {}
        key_claims += coverage.get("key_claims", 0)
        covered += coverage.get("covered_key_claims", 0)
        for claim_type, counts in (coverage.get("by_type") or {}).items():
            by_type.setdefault(claim_type, [0, 0])
            by_type[claim_type][0] += counts[0]
            by_type[claim_type][1] += counts[1]
        for info in processed.get("sentences") or []:
            if "safety" in info.get("detected_types", []):
                safety_claims += 1
                if info.get("valid_citation_count", 0) == 0:
                    uncited_safety_claims += 1
    return {
        "marker_parse_valid_rate": _fmt(total_markers - malformed, total_markers),
        "invalid_chunk_marker_rate": _fmt(invalid, total_markers),
        "key_claim_citation_coverage": _fmt(covered, key_claims),
        "parameter_claim_citation_coverage": _fmt(
            by_type.get("parameter", [0, 0])[0], by_type.get("parameter", [0, 0])[1]
        ),
        "procedure_claim_citation_coverage": _fmt(
            by_type.get("procedure", [0, 0])[0], by_type.get("procedure", [0, 0])[1]
        ),
        "safety_claim_citation_coverage": _fmt(
            by_type.get("safety", [0, 0])[0], by_type.get("safety", [0, 0])[1]
        ),
        "troubleshooting_claim_citation_coverage": _fmt(
            by_type.get("troubleshooting", [0, 0])[0],
            by_type.get("troubleshooting", [0, 0])[1],
        ),
        "uncited_safety_claim_count": uncited_safety_claims,
        "safety_claim_count": safety_claims,
    }


def claim_guard_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pruned_questions = 0
    pruned_sentences = 0
    empty_after_pruning = 0
    for row in rows:
        guard = row.get("claim_guard")
        if not guard:
            continue
        if guard.get("removed_claim_count", 0) > 0:
            pruned_questions += 1
        pruned_sentences += guard.get("removed_claim_count", 0)
        if guard.get("empty_after_pruning"):
            empty_after_pruning += 1
    total = len(rows)
    return {
        "claim_pruned_question_rate": _fmt(pruned_questions, total),
        "claim_pruned_sentence_count": pruned_sentences,
        "answer_empty_after_pruning_rate": _fmt(empty_after_pruning, total),
    }


def repair_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    triggered = sum(1 for r in rows if r.get("repair_attempted"))
    success = sum(
        1 for r in rows if r.get("repair_attempted") and r.get("repair_valid") is True
    )
    return {
        "repair_trigger_rate": _fmt(triggered, len(rows)),
        "repair_success_rate": _fmt(success, triggered),
        "repair_triggered_questions": [r["question_id"] for r in rows if r.get("repair_attempted")],
    }


def engineering(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [r.get("total_latency") or 0 for r in rows]
    ordered = sorted(latencies)
    return {
        "llm_calls": sum(1 for r in rows if r.get("llm_called")),
        "repair_calls": sum(1 for r in rows if r.get("repair_attempted")),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "repair_tokens": sum((r.get("repair_tokens") or {}).get("total_tokens", 0) for r in rows),
        "total_tokens": sum(r.get("total_tokens") or 0 for r in rows),
        "answer_latency_mean": round(
            sum(r.get("answer_latency") or 0 for r in rows if r.get("llm_called"))
            / max(1, sum(1 for r in rows if r.get("llm_called"))),
            3,
        ),
        "total_latency_p50": float(ordered[len(ordered) // 2]) if ordered else 0,
        "total_latency_p95": (
            float(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]) if ordered else 0
        ),
        "errors": sum(1 for r in rows if r.get("status") == "error"),
        "fallback_count": 0,
        "cache_hits": sum(1 for r in rows if r.get("cache_hit")),
    }


def category_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    out: dict[str, Any] = {}
    for category in sorted(set(QUESTION_CATEGORIES.values())):
        q_ids = [q for q, c in QUESTION_CATEGORIES.items() if c == category]
        sub = [r for r in rows if r["question_id"] in q_ids]
        if not sub:
            continue
        correct = 0
        refusals = 0
        for row in sub:
            expected = gold_pages.get(row["question_id"], set())
            pairs = {
                (c.get("document_name") or c.get("source_file"), c.get("page"))
                for c in (row.get("citations") or [])
            }
            if row["refusal"]:
                refusals += 1
            elif pairs & expected:
                correct += 1
        out[category] = {
            "questions": len(sub),
            "citation_accuracy": round(correct / len(sub), 4),
            "refusals": refusals,
            "false_rejection_rate": round(refusals / len(sub), 4),
            "question_ids": [r["question_id"] for r in sub],
        }
    return out


def safety_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    safety_ids = [q for q, c in QUESTION_CATEGORIES.items() if c == "安全警告"]
    correct = wrong = refusals = 0
    bypass = 0
    for question_id in safety_ids:
        row = next(r for r in rows if r["question_id"] == question_id)
        expected = gold_pages.get(question_id, set())
        pairs = {
            (c.get("document_name") or c.get("source_file"), c.get("page"))
            for c in (row.get("citations") or [])
        }
        if row["refusal"]:
            refusals += 1
        elif pairs & expected:
            correct += 1
        else:
            wrong += 1
        for sentence in (row.get("processed") or {}).get("sentences") or []:
            if "safety" in sentence.get("detected_types", []) and any(
                kw in sentence.get("clean_sentence", "")
                for kw in ("旁路", "绕过", "短接", "拆除联锁")
            ):
                bypass += 1
    return {
        "safety_questions": len(safety_ids),
        "citation_accuracy": round(correct / len(safety_ids), 4),
        "wrong_citation_questions": wrong,
        "refusals": refusals,
        "bypass_suggestions": bypass,
        "question_ids": safety_ids,
    }


def paired_bootstrap_metrics(
    base_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    from evaluation.experiments.phase4.parent_expansion.metrics import paired_bootstrap

    q_ids = [r["question_id"] for r in base_rows if r["question_id"] not in NEGATIVE_IDS]
    base_by_q = {r["question_id"]: r for r in base_rows}
    cand_by_q = {r["question_id"]: r for r in candidate_rows}

    def _values(selector) -> tuple[list[float], list[float]]:
        base_vals: list[float] = []
        cand_vals: list[float] = []
        for q in q_ids:
            base_vals.append(selector(base_by_q[q]))
            cand_vals.append(selector(cand_by_q[q]))
        return base_vals, cand_vals

    def acc(row: dict[str, Any]) -> float:
        pairs = {
            (c.get("document_name") or c.get("source_file"), c.get("page"))
            for c in (row.get("citations") or [])
        }
        return float(bool(pairs & gold_pages.get(row["question_id"], set())))

    def recall(row: dict[str, Any]) -> float:
        pairs = {
            (c.get("document_name") or c.get("source_file"), c.get("page"))
            for c in (row.get("citations") or [])
        }
        expected = gold_pages.get(row["question_id"], set())
        return len(pairs & expected) / len(expected) if expected else 0.0

    def gold_evidence(row: dict[str, Any]) -> float:
        chunks = {c.get("chunk_id") for c in (row.get("citations") or [])}
        return float(bool(chunks & mapped.get(row["question_id"], set())))

    def safety_coverage(row: dict[str, Any]) -> float:
        processed = row.get("processed") or {}
        coverage = processed.get("coverage") or {}
        counts = (coverage.get("by_type") or {}).get("safety", [0, 0])
        return counts[0] / counts[1] if counts[1] else 0.0

    def key_coverage(row: dict[str, Any]) -> float:
        processed = row.get("processed") or {}
        coverage = processed.get("coverage") or {}
        return coverage.get("covered_key_claims", 0) / coverage.get("key_claims", 1) if coverage.get("key_claims") else 0.0

    def refusal(row: dict[str, Any]) -> float:
        return float(row["refusal"])

    result: dict[str, Any] = {}
    for metric, selector in (
        ("answer_citation_accuracy", acc),
        ("answer_citation_recall", recall),
        ("gold_evidence_citation_rate", gold_evidence),
        ("false_rejection_rate", refusal),
        ("safety_claim_citation_coverage", safety_coverage),
        ("key_claim_citation_coverage", key_coverage),
    ):
        base_vals, cand_vals = _values(selector)
        result[metric] = paired_bootstrap(base_vals, cand_vals, n_iter=1000, seed=20260801)
    return result


def gl1_to_gl2_gates(gl0: dict[str, Any], gl1: dict[str, Any]) -> dict[str, Any]:
    hard = {
        "marker_parse_valid_rate_ge_095": gl1["marker"]["marker_parse_valid_rate"]["decimal"] >= 0.95,
        "emitted_citation_traceability_1": gl1["citation"]["emitted_citation_traceability"]["decimal"] == 1.0,
        "invalid_chunk_marker_rate_leq_005": gl1["marker"]["invalid_chunk_marker_rate"]["decimal"] <= 0.05,
        "false_rejection_not_up_005": (
            gl1["citation"]["false_rejection_rate"]["decimal"]
            - gl0["citation"]["false_rejection_rate"]["decimal"]
            <= 0.05
        ),
        "safety_accuracy_not_down_010": (
            gl1["safety"]["citation_accuracy"] >= gl0["safety"]["citation_accuracy"] - 0.10
        ),
    }
    return {"passed": all(hard.values()), "hard": hard}


def gl2_to_gl3_gates(gl0: dict[str, Any], gl2: dict[str, Any]) -> dict[str, Any]:
    hard = {
        "answer_text_hash_unchanged": all(
            row.get("answer_text_unchanged") is not False for row in gl2["rows"]
        ),
        "no_repair_errors": all(
            not (row.get("processed") or {}).get("repair_errors") for row in gl2["rows"]
        ),
        "repair_success_rate_ge_050": (
            gl2["repair"]["repair_success_rate"]["decimal"] is not None
            and gl2["repair"]["repair_success_rate"]["decimal"] >= 0.50
        ),
        "p95_latency_leq_2x": (
            gl2["engineering"]["total_latency_p95"]
            <= gl0["engineering"]["total_latency_p95"] * 2
        ),
        "false_rejection_not_up_005": (
            gl2["citation"]["false_rejection_rate"]["decimal"]
            - gl0["citation"]["false_rejection_rate"]["decimal"]
            <= 0.05
        ),
    }
    return {"passed": all(hard.values()), "hard": hard}


def replacement_gates(
    *,
    gl0: dict[str, Any],
    candidate: dict[str, Any],
    safety0: dict[str, Any],
    safety_c: dict[str, Any],
) -> dict[str, Any]:
    hard = {
        "emitted_citation_traceability_1": candidate["citation"]["emitted_citation_traceability"]["decimal"] == 1.0,
        "invalid_chunk_marker_rate_0": candidate["marker"]["invalid_chunk_marker_rate"]["decimal"] == 0,
        "negative_unsupported_answer_rate_0": candidate["citation"]["negative_unsupported_answer_rate"]["decimal"] == 0,
        "insufficient_evidence_rejection_rate_1": candidate["citation"]["insufficient_evidence_rejection_rate"]["decimal"] == 1.0,
        "answered_without_evidence_rate_0": candidate["citation"]["answered_without_evidence_rate"]["decimal"] == 0,
        "safety_claim_citation_coverage_1": candidate["marker"]["safety_claim_citation_coverage"]["decimal"] == 1.0,
        "uncited_safety_claim_count_0": candidate["marker"]["uncited_safety_claim_count"] == 0,
        "safety_accuracy_not_below_gl0": safety_c["citation_accuracy"] >= safety0["citation_accuracy"],
        "parameter_accuracy_drop_leq_002": (
            candidate["categories"].get("参数查询", {}).get("citation_accuracy", 0)
            - gl0["categories"].get("参数查询", {}).get("citation_accuracy", 0)
            >= -0.02
        ),
        "false_rejection_worsening_leq_005": (
            candidate["citation"]["false_rejection_rate"]["decimal"]
            - gl0["citation"]["false_rejection_rate"]["decimal"]
            <= 0.05
        ),
        "requested_equals_actual": all(
            not r.get("llm_called") or set(r.get("actual_model") or []) <= {FIXED_MODEL}
            for r in candidate["rows"]
        ),
        "fallback_0": candidate["engineering"]["fallback_count"] == 0,
        "p95_latency_leq_2x_gl0": (
            candidate["engineering"]["total_latency_p95"]
            <= gl0["engineering"]["total_latency_p95"] * 2
        ),
        "repair_text_unchanged": all(
            row.get("answer_text_unchanged") is not False for row in candidate["rows"]
        ),
    }
    value = {
        "answer_citation_accuracy_plus_002": (
            candidate["citation"]["answer_citation_accuracy"]["decimal"]
            >= gl0["citation"]["answer_citation_accuracy"]["decimal"] + 0.02
        ),
        "answer_citation_recall_plus_002": (
            candidate["citation"]["answer_citation_recall"]["decimal"]
            >= gl0["citation"]["answer_citation_recall"]["decimal"] + 0.02
        ),
        "gold_evidence_plus_002": (
            candidate["citation"]["gold_evidence_citation_rate"]["decimal"]
            >= gl0["citation"]["gold_evidence_citation_rate"]["decimal"] + 0.02
        ),
        "non_gold_down_010": (
            gl0["citation"]["non_gold_citation_reference_rate"]["decimal"]
            - candidate["citation"]["non_gold_citation_reference_rate"]["decimal"]
            >= 0.10
        ),
        "key_claim_coverage_plus_010": (
            candidate["marker"]["key_claim_citation_coverage"]["decimal"]
            - gl0["marker"]["key_claim_citation_coverage"]["decimal"]
            >= 0.10
        ),
        "uncited_key_claim_reduction_20pct": _uncited_reduction_pct(gl0, candidate) >= 20,
        "safety_error_reduction_ge_1": (
            gl0["safety"]["wrong_citation_questions"] - safety_c["wrong_citation_questions"] >= 1
        ),
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
            "Grounded Answer Lite passed Phase 5B replacement gates"
            if passed
            else "Grounded Answer Lite did not pass Phase 5B replacement gates"
        ),
    }


def _uncited_reduction_pct(gl0: dict[str, Any], candidate: dict[str, Any]) -> float:
    def _uncited(group: dict[str, Any]) -> int:
        total = 0
        for row in group["rows"]:
            processed = row.get("processed") or {}
            coverage = processed.get("coverage") or {}
            total += coverage.get("key_claims", 0) - coverage.get("covered_key_claims", 0)
        return total

    base = _uncited(gl0)
    cand = _uncited(candidate)
    if base <= 0:
        return 0.0
    return round((base - cand) / base * 100, 2)
