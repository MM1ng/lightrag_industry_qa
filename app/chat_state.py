"""Pure Python chat session state for the Streamlit chatbot UI.

Zero dependency on streamlit / industrial_rag / lightrag / openai.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

ChatQueryMode = Literal["mix", "hybrid", "local", "global", "naive"]
MessageStatus = Literal["success", "insufficient_evidence", "error"]

SUPPORTED_CHAT_QUERY_MODES: frozenset[str] = frozenset(
    {"mix", "hybrid", "local", "global", "naive"}
)
SUPPORTED_MESSAGE_STATUSES: frozenset[str] = frozenset(
    {"success", "insufficient_evidence", "error"}
)


@dataclass(frozen=True, slots=True)
class ChatCitation:
    """UI-layer immutable citation snapshot.

    Converted from industrial_rag.citation_formatter.Citation at the Streamlit
    boundary without importing that package here.
    """

    source_file: str
    page_number: int
    chunk_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_file, str) or not self.source_file.strip():
            raise ValueError("source_file 不能为空")
        object.__setattr__(self, "source_file", self.source_file.strip())

        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int):
            raise TypeError("page_number 必须为 int（不能为 bool）")
        if self.page_number < 1:
            raise ValueError("page_number 必须为正整数")

        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ValueError("chunk_id 不能为空")
        object.__setattr__(self, "chunk_id", self.chunk_id.strip())

    @property
    def display(self) -> str:
        """Match Citation.display format: [filename，第X页]."""
        return f"[{self.source_file}，第{self.page_number}页]"


@dataclass(frozen=True, slots=True)
class UserMessage:
    """User chat message. role and message_id are fixed by the type."""

    content: str
    message_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    role: Literal["user"] = field(default="user", init=False)

    def __post_init__(self) -> None:
        _validate_and_normalize_content(self, "content")
        _validate_created_at(self)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """Assistant chat message with per-message mode/latency/citations/status.

    status="error" allows mode=None and latency_seconds=None.
    status="success" / "insufficient_evidence" require a non-None mode.
    """

    content: str
    mode: ChatQueryMode | None = None
    latency_seconds: float | None = None
    citations: tuple[ChatCitation, ...] = ()
    status: MessageStatus = "success"
    message_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    role: Literal["assistant"] = field(default="assistant", init=False)

    def __post_init__(self) -> None:
        _validate_and_normalize_content(self, "content")
        _validate_created_at(self)
        _validate_and_enforce_status(self)
        _validate_and_enforce_mode(self)
        _validate_latency(self)
        _enforce_tuple_citations(self)
        _validate_citation_items(self)

    @property
    def citation_count(self) -> int:
        return len(self.citations)


ChatMessage = UserMessage | AssistantMessage
ChatSession = list[ChatMessage]


def _validate_and_normalize_content(
    obj: UserMessage | AssistantMessage,
    field_name: str,
) -> None:
    raw = getattr(obj, field_name)
    if not isinstance(raw, str):
        raise TypeError(f"{field_name} 必须为字符串")
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("消息内容不能为空")
    if cleaned != raw:
        object.__setattr__(obj, field_name, cleaned)


def _validate_created_at(obj: UserMessage | AssistantMessage) -> None:
    dt = obj.created_at
    if not isinstance(dt, datetime):
        raise TypeError("created_at 必须为 datetime 对象")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("created_at 必须包含有效时区信息")


def _validate_and_enforce_status(msg: AssistantMessage) -> None:
    if msg.status not in SUPPORTED_MESSAGE_STATUSES:
        raise ValueError(
            f"status 必须为 {sorted(SUPPORTED_MESSAGE_STATUSES)} 之一，收到: {msg.status!r}"
        )


def _validate_and_enforce_mode(msg: AssistantMessage) -> None:
    if msg.mode is not None and msg.mode not in SUPPORTED_CHAT_QUERY_MODES:
        raise ValueError(
            f"mode 必须为 {sorted(SUPPORTED_CHAT_QUERY_MODES)} 之一，收到: {msg.mode!r}"
        )
    if msg.status in ("success", "insufficient_evidence") and msg.mode is None:
        raise ValueError(f"status='{msg.status}' 时 mode 不能为 None")


def _validate_latency(msg: AssistantMessage) -> None:
    if msg.latency_seconds is None:
        return
    value = msg.latency_seconds
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("latency_seconds 必须为数字（不能为 bool）")
    if math.isnan(value):
        raise ValueError("latency_seconds 不能为 NaN")
    if math.isinf(value):
        raise ValueError("latency_seconds 不能为 Infinity")
    if value < 0:
        raise ValueError("latency_seconds 不能为负数")


def _enforce_tuple_citations(msg: AssistantMessage) -> None:
    current = msg.citations
    if not isinstance(current, tuple):
        object.__setattr__(msg, "citations", tuple(current or ()))


def _validate_citation_items(msg: AssistantMessage) -> None:
    for index, citation in enumerate(msg.citations):
        if not isinstance(citation, ChatCitation):
            raise TypeError(
                f"citations[{index}] 必须为 ChatCitation，收到: {type(citation).__name__}"
            )


def create_empty_session() -> ChatSession:
    """Return a new empty session list."""
    return []


def add_user_message(
    session: Sequence[ChatMessage],
    content: str,
) -> tuple[ChatSession, UserMessage]:
    """Append a user message without mutating the original session."""
    msg = UserMessage(content=content)
    return [*session, msg], msg


def add_assistant_message(
    session: Sequence[ChatMessage],
    content: str,
    *,
    mode: ChatQueryMode | None = None,
    latency_seconds: float | None = None,
    citations: Iterable[ChatCitation] = (),
    status: MessageStatus = "success",
) -> tuple[ChatSession, AssistantMessage]:
    """Append an assistant message without mutating the original session."""
    msg = AssistantMessage(
        content=content,
        mode=mode,
        latency_seconds=latency_seconds,
        citations=tuple(citations or ()),
        status=status,
    )
    return [*session, msg], msg


def add_error_message(
    session: Sequence[ChatMessage],
    content: str,
) -> tuple[ChatSession, AssistantMessage]:
    """Shortcut for error assistant messages with status='error'."""
    return add_assistant_message(
        session,
        content,
        mode=None,
        latency_seconds=None,
        citations=(),
        status="error",
    )


def clear_session() -> ChatSession:
    """Return an empty session. Does not close Runtime or clear caches."""
    return []


def session_message_count(session: Sequence[ChatMessage]) -> int:
    """Total message count (user + assistant)."""
    return len(session)
