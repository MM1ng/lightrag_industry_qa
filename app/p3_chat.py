"""Pure adapters between P3 API results and the existing chat-state model."""

from __future__ import annotations

from collections.abc import Sequence

from app.api_client import ApiQueryResult
from app.chat_state import (
    AssistantMessage,
    ChatCitation,
    ChatMessage,
    ChatSession,
    UserMessage,
    add_assistant_message,
    add_error_message,
)


def build_p3_history(session: Sequence[ChatMessage], *, limit: int = 6) -> list[dict[str, str]]:
    """Return the most recent conversation messages accepted by the P3 API."""
    history: list[dict[str, str]] = []
    for message in session:
        if isinstance(message, UserMessage):
            history.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage) and message.status != "error":
            history.append({"role": "assistant", "content": message.content})
    bounded_limit = min(max(limit, 0), 6)
    return history[-bounded_limit:] if bounded_limit else []


def append_p3_answer(
    session: Sequence[ChatMessage],
    result: ApiQueryResult,
) -> tuple[ChatSession, AssistantMessage]:
    """Append one safe P3 response using the UI's existing chat-state contract."""
    if result.status == "failed":
        return add_error_message(session, result.answer)

    citations = tuple(
        ChatCitation(
            source_file=citation.source_file,
            page_number=citation.page_number,
            chunk_id=citation.chunk_id,
        )
        for citation in result.citations
    )
    status = "insufficient_evidence" if result.status == "insufficient_evidence" else "success"
    return add_assistant_message(
        session,
        result.answer,
        mode="mix",
        latency_seconds=result.latency_ms / 1000.0,
        citations=citations,
        status=status,
    )
