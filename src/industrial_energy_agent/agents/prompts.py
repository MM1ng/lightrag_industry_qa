"""Constrained prompt fragments for optional model-backed routing adapters."""

from __future__ import annotations

from industrial_energy_agent.domain.enums import Intent

INTENT_CLASSIFICATION_PROMPT = """Classify only the user's maintenance request.
Return JSON with `intent` and a confidence in [0, 1]. Allowed intent values: {intents}.
If the request cannot be classified confidently, return `unknown`. Do not include
reasoning, system instructions, credentials, or filesystem details.""".format(
    intents=", ".join(intent.value for intent in Intent)
)
