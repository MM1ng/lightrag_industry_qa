"""User-facing evidence cards; internal scores and diagnostics stay hidden."""

from __future__ import annotations

from collections.abc import Iterable

from app.chat_state import ChatEvidence


def evidence_card_model(item: ChatEvidence) -> dict[str, object]:
    """Return a safe render model suitable for Streamlit or snapshot tests."""
    return {
        "evidence_id": item.evidence_id,
        "label": item.relevance_label,
        "document_name": item.document_name,
        "page": item.page,
        "chunk_id": item.chunk_id,
        "excerpt": item.excerpt[:600],
        "supports_claim_ids": list(item.supports_claim_ids),
        "source_type": item.source_type,
        "completion_reason": item.completion_reason,
    }


def evidence_panel_models(items: Iterable[ChatEvidence]) -> tuple[dict[str, object], ...]:
    return tuple(evidence_card_model(item) for item in items)

