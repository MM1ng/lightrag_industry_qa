"""Deterministic metrics and paired gate for the conversation E2E experiment."""

from __future__ import annotations

from typing import Any

from industrial_rag.conversation.retrieval_evaluation import compute_retrieval_metrics


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "metric_unavailable", "reason": reason, "value": None}


def _retrieval(arm: dict[str, Any], gold: list[str]) -> dict[str, float]:
    return compute_retrieval_metrics(list(arm.get("retrieved_chunk_ids", ())), gold, ks=(5, 10))


def _answer_metrics(arm: dict[str, Any]) -> dict[str, Any]:
    status = str(arm.get("answer_status") or "error")
    answered = status in {"success", "partial_answer"}
    refusal = status in {"insufficient_evidence", "safety_blocked"}
    citations = arm.get("citations", [])
    points = arm.get("answer_points", [])
    return {
        "supporting_recall": _unavailable("frozen Development dataset has no expected answer-point support labels"),
        "false_rejection_rate": {"status": "available", "value": float(refusal)},
        "question_level_citation_accuracy": _unavailable("frozen Development dataset has no question-level citation gold"),
        "unsupported_answer_rate": {
            "status": "available",
            "value": float(any(point.get("support_status") == "unsupported" for point in points)),
        },
        "expected_answer_coverage": _unavailable("frozen Development dataset has no trusted reference answer"),
        "answer_status": status,
        "answered": answered,
        "citation_count": len(citations),
    }


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    gold = [str(item) for item in case.get("gold_chunk_ids", ())]
    result: dict[str, Any] = {"case_id": case.get("case_id"), "baseline": {}, "candidate": {}}
    for arm_name in ("baseline", "candidate"):
        arm = case[arm_name]
        result[arm_name] = {
            **_retrieval(arm, gold),
            **_answer_metrics(arm),
        }
    result["delta"] = {
        name: result["candidate"].get(name) - result["baseline"].get(name)
        for name in ("hit_recall_at_5", "hit_recall_at_10", "evidence_recall_at_5", "evidence_recall_at_10", "mrr_at_5", "mrr_at_10")
    }
    result["improved"] = result["delta"]["hit_recall_at_5"] > 0 or result["delta"]["mrr_at_5"] > 0
    result["regressed"] = result["delta"]["hit_recall_at_5"] < 0 and result["delta"]["mrr_at_5"] < 0
    result["unchanged"] = not result["improved"] and not result["regressed"]
    return result


def aggregate_arm(rows: list[dict[str, Any]], arm_name: str) -> dict[str, Any]:
    names = ("hit_recall_at_5", "hit_recall_at_10", "evidence_recall_at_5", "evidence_recall_at_10", "mrr_at_5", "mrr_at_10")
    return {
        "denominator": len(rows),
        **{name: sum(float(row[arm_name][name]) for row in rows) / len(rows) if rows else None for name in names},
        "supporting_recall": _aggregate_optional(rows, arm_name, "supporting_recall"),
        "false_rejection_rate": _aggregate_optional(rows, arm_name, "false_rejection_rate"),
        "question_level_citation_accuracy": _aggregate_optional(rows, arm_name, "question_level_citation_accuracy"),
        "unsupported_answer_rate": _aggregate_optional(rows, arm_name, "unsupported_answer_rate"),
        "expected_answer_coverage": _aggregate_optional(rows, arm_name, "expected_answer_coverage"),
    }


def _aggregate_optional(rows: list[dict[str, Any]], arm_name: str, name: str) -> dict[str, Any]:
    values = [row[arm_name][name]["value"] for row in rows if row[arm_name][name]["status"] == "available"]
    if not values:
        reason = next((row[arm_name][name]["reason"] for row in rows), "no available values")
        return _unavailable(reason)
    return {"status": "available", "value": sum(values) / len(values), "denominator": len(values)}


def paired_case_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {name: sum(1 for row in rows if row[name]) for name in ("improved", "unchanged", "regressed")}


def classify_failure_layer(case: dict[str, Any]) -> str | None:
    for arm_name in ("candidate", "baseline"):
        arm = case.get(arm_name, {})
        if arm.get("metric_error"):
            return arm.get("failure_layer") or "Answer Generation Error"
        if arm.get("grounding_failure_categories"):
            return "Grounding Error"
        if arm.get("answer_status") == "insufficient_evidence":
            return "Refusal Decision Error"
    return None


def evaluate_gate(summary: dict[str, Any]) -> dict[str, Any]:
    baseline = summary["baseline"]
    candidate = summary["candidate"]
    if summary.get("blocked"):
        return {"status": "BLOCKED", "reasons": list(summary.get("blocked_reasons", ())) }
    reasons: list[str] = []
    if candidate["supporting_recall"] <= baseline["supporting_recall"]:
        reasons.append("supporting_recall_not_improved")
    if candidate["false_rejection_rate"] > baseline["false_rejection_rate"]:
        reasons.append("false_rejection_rate_regressed")
    if candidate["question_level_citation_accuracy"] < baseline["question_level_citation_accuracy"]:
        reasons.append("citation_accuracy_regressed")
    if candidate["unsupported_answer_rate"] > baseline["unsupported_answer_rate"]:
        reasons.append("unsupported_answer_rate_regressed")
    if candidate["expected_answer_coverage"] <= baseline["expected_answer_coverage"]:
        reasons.append("expected_coverage_not_improved")
    for metric in ("faithfulness", "response_relevancy"):
        if candidate[metric]["mean"] < baseline[metric]["mean"] - 0.05:
            reasons.append(f"{metric}_substantially_regressed")
    if summary.get("severe_regressions"):
        reasons.append("severe_regression")
    if summary.get("severe_regressions"):
        return {"status": "R3_FAIL", "reasons": reasons}
    if reasons:
        return {"status": "R3_MIXED", "reasons": reasons}
    return {"status": "R3_PASS", "reasons": []}
