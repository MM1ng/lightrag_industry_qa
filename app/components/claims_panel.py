"""Claim render models with exact per-claim citation IDs."""

from __future__ import annotations

from collections.abc import Iterable

from app.chat_state import ChatClaim


def claim_models(items: Iterable[ChatClaim]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "claim_id": item.claim_id,
            "text": item.text,
            "citation_ids": list(item.citation_ids),
            "evidence_ids": list(item.evidence_ids),
        }
        for item in items
    )

