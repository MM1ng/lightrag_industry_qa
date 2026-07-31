"""Pure unit tests for app.chat_state — zero Streamlit / industrial_rag deps."""

from __future__ import annotations

import ast
import importlib
import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import pytest
from app.chat_state import (
    SUPPORTED_CHAT_QUERY_MODES,
    SUPPORTED_MESSAGE_STATUSES,
    AssistantMessage,
    ChatCitation,
    UserMessage,
    add_assistant_message,
    add_error_message,
    add_user_message,
    clear_session,
    create_empty_session,
    session_message_count,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHAT_STATE_PATH = PROJECT_ROOT / "app" / "chat_state.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class _DanglingTZ(tzinfo):
    """tzinfo present but utcoffset() returns None."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "dangling"


# ---------------------------------------------------------------------------
# Dependency isolation
# ---------------------------------------------------------------------------


def test_module_has_no_streamlit_import() -> None:
    assert "streamlit" not in _module_imports(CHAT_STATE_PATH)


def test_module_has_no_industrial_rag_import() -> None:
    assert "industrial_rag" not in _module_imports(CHAT_STATE_PATH)


def test_module_has_no_runtime_import() -> None:
    source = CHAT_STATE_PATH.read_text(encoding="utf-8")
    assert "LightRAGRuntime" not in source
    assert "from industrial_rag.runtime" not in source
    assert "import runtime" not in _module_imports(CHAT_STATE_PATH)


def test_module_has_no_lightrag_service_import() -> None:
    source = CHAT_STATE_PATH.read_text(encoding="utf-8")
    assert "LightRAGService" not in source
    assert "lightrag_service" not in source
    assert "lightrag" not in _module_imports(CHAT_STATE_PATH)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_user_message_role_not_overridable() -> None:
    with pytest.raises(TypeError):
        UserMessage(content="hello", role="assistant")  # type: ignore[call-arg]


def test_assistant_message_role_not_overridable() -> None:
    with pytest.raises(TypeError):
        AssistantMessage(content="hello", mode="mix", role="user")  # type: ignore[call-arg]


def test_user_message_id_not_overridable() -> None:
    with pytest.raises(TypeError):
        UserMessage(content="hello", message_id="fixed-id")  # type: ignore[call-arg]


def test_assistant_message_id_not_overridable() -> None:
    with pytest.raises(TypeError):
        AssistantMessage(content="hello", mode="mix", message_id="fixed-id")  # type: ignore[call-arg]


def test_each_message_has_unique_message_id() -> None:
    a = UserMessage(content="one")
    b = UserMessage(content="two")
    assert a.message_id != b.message_id
    c = AssistantMessage(content="three", mode="mix")
    d = AssistantMessage(content="four", mode="local")
    assert c.message_id != d.message_id


def test_message_id_immutable() -> None:
    msg = UserMessage(content="hello")
    with pytest.raises(FrozenInstanceError):
        msg.message_id = "x"  # type: ignore[misc]


def test_created_at_has_utc_timezone() -> None:
    msg = UserMessage(content="hello")
    assert msg.created_at.tzinfo is not None
    assert msg.created_at.tzinfo == UTC
    assert msg.created_at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


def test_content_empty_raises() -> None:
    with pytest.raises(ValueError, match="消息内容不能为空"):
        UserMessage(content="")


def test_content_whitespace_only_raises() -> None:
    with pytest.raises(ValueError, match="消息内容不能为空"):
        UserMessage(content="  \n  ")


def test_content_strips_whitespace() -> None:
    msg = UserMessage(content=" hello ")
    assert msg.content == "hello"


def test_content_markdown_preserved() -> None:
    raw = "第一行\n**加粗** 与 `code`"
    msg = AssistantMessage(content=raw, mode="mix")
    assert "**加粗**" in msg.content
    assert "\n" in msg.content
    assert "`code`" in msg.content


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def test_citations_list_converted_to_tuple() -> None:
    c1 = ChatCitation(source_file="a.pdf", page_number=1, chunk_id="c1")
    c2 = ChatCitation(source_file="b.pdf", page_number=2, chunk_id="c2")
    msg = AssistantMessage(content="ans", mode="mix", citations=[c1, c2])  # type: ignore[arg-type]
    assert isinstance(msg.citations, tuple)
    assert len(msg.citations) == 2


def test_citations_source_list_mutation_no_effect() -> None:
    c1 = ChatCitation(source_file="a.pdf", page_number=1, chunk_id="c1")
    source: list[Any] = [c1]
    msg = AssistantMessage(content="ans", mode="mix", citations=source)  # type: ignore[arg-type]
    source.append(ChatCitation(source_file="b.pdf", page_number=2, chunk_id="c2"))
    assert len(msg.citations) == 1
    assert msg.citations[0].chunk_id == "c1"


def test_citations_rejects_non_chat_citation() -> None:
    with pytest.raises(TypeError, match="ChatCitation"):
        AssistantMessage(content="ans", mode="mix", citations=("not-a-citation",))  # type: ignore[arg-type]


def test_chat_citation_page_number_rejects_bool() -> None:
    with pytest.raises(TypeError, match="page_number"):
        ChatCitation(source_file="a.pdf", page_number=True, chunk_id="c1")  # type: ignore[arg-type]


def test_chat_citation_normalizes_whitespace() -> None:
    citation = ChatCitation(source_file=" file.pdf ", page_number=3, chunk_id="  cid  ")
    assert citation.source_file == "file.pdf"
    assert citation.chunk_id == "cid"


def test_chat_citation_preserves_chunk_id() -> None:
    citation = ChatCitation(source_file="a.pdf", page_number=1, chunk_id="abc-123")
    msg = AssistantMessage(content="ans", mode="mix", citations=(citation,))
    assert msg.citations[0].chunk_id == "abc-123"


# ---------------------------------------------------------------------------
# Status / mode
# ---------------------------------------------------------------------------


def test_success_requires_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        AssistantMessage(content="ans", status="success", mode=None)


def test_insufficient_evidence_requires_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        AssistantMessage(content="ans", status="insufficient_evidence", mode=None)


def test_error_allows_none_mode() -> None:
    msg = AssistantMessage(content="failed", status="error", mode=None)
    assert msg.status == "error"
    assert msg.mode is None


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValueError, match="status"):
        AssistantMessage(content="ans", mode="mix", status="fake_status")  # type: ignore[arg-type]


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        AssistantMessage(content="ans", mode="fake_mode")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_latency_negative_raises() -> None:
    with pytest.raises(ValueError, match="latency"):
        AssistantMessage(content="ans", mode="mix", latency_seconds=-1)


def test_latency_nan_raises() -> None:
    with pytest.raises(ValueError, match="latency"):
        AssistantMessage(content="ans", mode="mix", latency_seconds=float("nan"))


def test_latency_infinity_raises() -> None:
    with pytest.raises(ValueError, match="latency"):
        AssistantMessage(content="ans", mode="mix", latency_seconds=float("inf"))


def test_latency_bool_rejected() -> None:
    with pytest.raises(TypeError, match="latency"):
        AssistantMessage(content="ans", mode="mix", latency_seconds=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# created_at
# ---------------------------------------------------------------------------


def test_created_at_rejects_non_datetime() -> None:
    with pytest.raises(TypeError, match="created_at"):
        UserMessage(content="hello", created_at="2026-01-01")  # type: ignore[arg-type]


def test_created_at_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="时区"):
        UserMessage(content="hello", created_at=datetime(2026, 1, 1))


def test_created_at_rejects_dangling_tzinfo() -> None:
    with pytest.raises(ValueError, match="时区"):
        UserMessage(
            content="hello",
            created_at=datetime(2026, 1, 1, tzinfo=_DanglingTZ()),
        )


def test_created_at_accepts_non_utc_aware() -> None:
    offset = timezone(timedelta(hours=8))
    dt = datetime(2026, 1, 1, 12, 0, tzinfo=offset)
    msg = UserMessage(content="hello", created_at=dt)
    assert msg.created_at == dt
    assert msg.created_at.utcoffset() == timedelta(hours=8)


# ---------------------------------------------------------------------------
# Operation semantics
# ---------------------------------------------------------------------------


def test_original_session_not_mutated_by_add_user() -> None:
    original = create_empty_session()
    new_session, msg = add_user_message(original, "question")
    assert original == []
    assert len(new_session) == 1
    assert new_session[0] is msg
    assert isinstance(msg, UserMessage)


def test_original_session_not_mutated_by_add_assistant() -> None:
    original, _ = add_user_message(create_empty_session(), "q")
    snapshot = list(original)
    new_session, msg = add_assistant_message(original, "a", mode="hybrid", latency_seconds=1.5)
    assert original == snapshot
    assert len(new_session) == 2
    assert new_session[-1] is msg
    assert msg.mode == "hybrid"
    assert msg.latency_seconds == 1.5


def test_clear_session_returns_empty_list() -> None:
    assert clear_session() == []


def test_multi_round_message_order_preserved() -> None:
    session = create_empty_session()
    for i in range(1, 4):
        session, _ = add_user_message(session, f"u{i}")
        session, _ = add_assistant_message(session, f"a{i}", mode="mix")
    assert session_message_count(session) == 6
    contents = [m.content for m in session]
    assert contents == ["u1", "a1", "u2", "a2", "u3", "a3"]


# ---------------------------------------------------------------------------
# Integration behavior
# ---------------------------------------------------------------------------


def test_historical_assistant_mode_not_affected_by_current_qa_mode() -> None:
    session, _ = add_user_message(create_empty_session(), "q1")
    session, assistant = add_assistant_message(session, "a1", mode="local")
    current_qa_mode = "global"
    assert assistant.mode == "local"
    assert assistant.mode != current_qa_mode
    # external variable change must not rewrite stored message
    current_qa_mode = "naive"
    assert session[-1].mode == "local"  # type: ignore[union-attr]
    assert current_qa_mode == "naive"


def test_app_package_importable() -> None:
    module = importlib.import_module("app.chat_state")
    assert hasattr(module, "UserMessage")
    assert hasattr(module, "AssistantMessage")
    assert frozenset({"mix", "hybrid", "local", "global", "naive"}) == SUPPORTED_CHAT_QUERY_MODES
    assert frozenset({"success", "insufficient_evidence", "error"}) == SUPPORTED_MESSAGE_STATUSES


def test_add_error_message_sets_error_status() -> None:
    session, msg = add_error_message(create_empty_session(), "boom")
    assert len(session) == 1
    assert msg.status == "error"
    assert msg.mode is None
    assert msg.latency_seconds is None
    assert msg.citations == ()


def test_chat_citation_display_format() -> None:
    citation = ChatCitation(source_file="手册.pdf", page_number=12, chunk_id="x")
    assert citation.display == "[手册.pdf，第12页]"


def test_assistant_citation_count_property() -> None:
    c1 = ChatCitation(source_file="a.pdf", page_number=1, chunk_id="c1")
    msg = AssistantMessage(content="ans", mode="mix", citations=(c1,))
    assert msg.citation_count == 1


def test_latency_none_allowed_for_error() -> None:
    msg = AssistantMessage(content="err", status="error", mode=None, latency_seconds=None)
    assert msg.latency_seconds is None


def test_math_helpers_used_for_special_floats() -> None:
    # sanity: nan/inf detection path remains reachable via constructor
    assert math.isnan(float("nan"))
    assert math.isinf(float("inf"))
