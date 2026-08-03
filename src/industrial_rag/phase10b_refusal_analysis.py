"""Explainable evidence/refusal state analysis for Phase 10B."""

from __future__ import annotations

from typing import Any, Literal

RefusalState = Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"]


def classify_refusal_state(case: dict[str, Any]) -> RefusalState:
    response = case.get("response") or {}
    if response.get("status") == "safety_blocked":
        return "safety_blocked"
    if response.get("status") == "insufficient_evidence":
        return "insufficient_evidence"
    expected = {item["chunk_id"] for item in case["golden"].get("expected_evidence", [])}
    cited = {item.get("chunk_id") for item in response.get("citations", [])}
    if expected and cited and not expected <= cited:
        return "partial_answer"
    return "success"


def explain_case(case: dict[str, Any]) -> dict[str, Any]:
    expected = {item["chunk_id"] for item in case["golden"].get("expected_evidence", [])}
    selected = {item["chunk_id"] for item in (case.get("trace") or {}).get("final_selected_chunks", [])}
    cited = {item.get("chunk_id") for item in case.get("response", {}).get("citations", [])}
    return {
        "question_id": case["question_id"],
        "split": case["golden"]["split"],
        "state": classify_refusal_state(case),
        "expected_evidence_count": len(expected),
        "selected_evidence_count": len(selected),
        "selected_expected_count": len(expected & selected),
        "cited_expected_count": len(expected & cited),
        "answerable": case["golden"]["answerable"],
    }
