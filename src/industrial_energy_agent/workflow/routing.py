"""Pure routing helpers for the LangGraph workflow."""

from __future__ import annotations

from collections.abc import Mapping

from industrial_energy_agent.agents.intent_router import classify_intent
from industrial_energy_agent.domain.enums import Intent


def route_intent(user_query: str) -> Intent:
    """Classify a request without model calls or implicit confidence guesses."""

    return classify_intent(user_query)


def route_after_precheck(state: Mapping[str, object]) -> str:
    return "finalize_status" if state.get("safety_decision") == "RESTRICTED" else "intent_router"


def route_after_evaluation(state: Mapping[str, object]) -> str:
    return "rewrite" if state.get("evidence_decision") == "REWRITE" else "finalize_status"
