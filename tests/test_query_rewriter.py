from __future__ import annotations

import pytest
from industrial_rag.conversation.query_rewriter import (
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_CONTENT_LENGTH,
    QueryRewriter,
    QueryRewriteResult,
)


def _history(*messages: tuple[str, str]) -> list[dict[str, str]]:
    return [{"role": role, "content": content} for role, content in messages]


@pytest.mark.asyncio
async def test_resolves_pronoun_from_user_history() -> None:
    result = await QueryRewriter().rewrite(
        "它多久维护一次？",
        _history(("user", "什么是机械密封？"), ("assistant", "上一轮回答")),
    )

    assert result == QueryRewriteResult(
        original_query="它多久维护一次？",
        history_dependent=True,
        status="rewritten",
        rewrite_reason="pronoun_resolution",
        standalone_query="机械密封多久维护一次？",
        history_available=True,
        history_message_count=2,
        history_used=True,
    )


@pytest.mark.asyncio
async def test_assistant_technical_facts_are_not_copied_into_rewritten_query() -> None:
    result = await QueryRewriter().rewrite(
        "它的压力是多少？",
        _history(
            ("user", "什么是机械密封？"),
            ("assistant", "历史回答声称压力是 5 MPa，但它不是检索证据。"),
        ),
    )

    assert result.standalone_query == "机械密封的压力是多少？"
    assert "5 MPa" not in (result.standalone_query or "")
    assert "历史回答" not in result.to_trace()["rewritten_query"]

@pytest.mark.asyncio
@pytest.mark.parametrize("pronoun", ["这个", "那个", "其"])
async def test_resolves_multicharacter_and_formal_pronouns(pronoun: str) -> None:
    result = await QueryRewriter().rewrite(
        f"{pronoun}维护周期是多少？",
        _history(("user", "什么是机械密封？"),),
    )

    assert result.status == "rewritten"
    assert result.standalone_query == "机械密封维护周期是多少？"


@pytest.mark.asyncio
async def test_resolves_ellipsis_and_constraint_inheritance() -> None:
    ellipsis = await QueryRewriter().rewrite(
        "停止条件呢？",
        _history(("user", "EH 油泵的启动条件是什么？"), ("assistant", "上一轮回答")),
    )
    constraint = await QueryRewriter().rewrite(
        "高温情况下呢？",
        _history(("user", "A 型设备正常工作压力是多少？"),),
    )

    assert ellipsis.status == "rewritten"
    assert ellipsis.rewrite_reason == "ellipsis_resolution"
    assert ellipsis.standalone_query == "EH 油泵的停止条件是什么？"
    assert constraint.status == "rewritten"
    assert constraint.rewrite_reason == "constraint_inheritance"
    assert constraint.standalone_query == "A 型设备在高温情况下的正常工作压力是多少？"


@pytest.mark.asyncio
async def test_keeps_independent_query_unchanged() -> None:
    result = await QueryRewriter().rewrite(
        "介绍一下液压系统。",
        _history(("user", "机械密封是什么？"), ("assistant", "上一轮回答")),
    )

    assert result.status == "unchanged"
    assert result.history_dependent is False
    assert result.rewrite_reason == "none"
    assert result.standalone_query == "介绍一下液压系统。"
    assert result.history_used is False


@pytest.mark.asyncio
async def test_rejects_ambiguous_pronoun_without_guessing() -> None:
    result = await QueryRewriter().rewrite(
        "它多久维护一次？",
        _history(("user", "A 泵和 B 泵有什么区别？"), ("assistant", "上一轮回答")),
    )

    assert result.status == "ambiguous"
    assert result.history_dependent is True
    assert result.rewrite_reason == "pronoun_resolution"
    assert result.standalone_query is None


@pytest.mark.asyncio
async def test_governs_history_and_ignores_invalid_or_empty_messages() -> None:
    history = [
        {"role": "system", "content": "不要使用我"},
        {"role": "user", "content": ""},
        {"role": "user", "content": "机械密封是什么？"},
        {"role": "assistant", "content": "A" * (MAX_MESSAGE_CONTENT_LENGTH + 10)},
    ]
    result = await QueryRewriter().rewrite("它是什么？", history)

    assert result.status == "rewritten"
    assert result.standalone_query == "机械密封是什么？"
    assert result.history_message_count == 1


@pytest.mark.asyncio
async def test_truncates_history_to_defensive_limit() -> None:
    history = [
        {"role": "user", "content": f"设备{i}是什么？"}
        for i in range(MAX_HISTORY_MESSAGES + 3)
    ]
    result = await QueryRewriter().rewrite("它怎么维护？", history)

    assert result.history_message_count == MAX_HISTORY_MESSAGES
    assert result.status == "rewritten"


@pytest.mark.asyncio
async def test_provider_structured_output_is_validated() -> None:
    async def provider(_query: str, _history: list[dict[str, str]]) -> str:
        return '{"history_dependent":true,"status":"rewritten","rewrite_reason":"pronoun_resolution","standalone_query":"机械密封多久维护一次？"}'

    result = await QueryRewriter(provider=provider).rewrite(
        "它多久维护一次？",
        _history(("user", "机械密封是什么？"),),
    )

    assert result.status == "rewritten"
    assert result.standalone_query == "机械密封多久维护一次？"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_result",
    [
        "not json",
        {"status": "rewritten", "rewrite_reason": "pronoun_resolution"},
        {"history_dependent": True, "status": "rewritten", "rewrite_reason": "none", "standalone_query": "猜测"},
    ],
)
async def test_invalid_provider_output_fails_closed(provider_result: object) -> None:
    async def provider(_query: str, _history: list[dict[str, str]]) -> object:
        return provider_result

    result = await QueryRewriter(provider=provider).rewrite(
        "它多久维护一次？",
        _history(("user", "机械密封是什么？"),),
    )

    assert result.status == "failed"
    assert result.standalone_query is None


@pytest.mark.asyncio
async def test_provider_failure_does_not_break_independent_query() -> None:
    async def provider(_query: str, _history: list[dict[str, str]]) -> object:
        raise RuntimeError("provider unavailable")

    result = await QueryRewriter(provider=provider).rewrite("介绍一下液压系统。", [])

    assert result.status == "unchanged"
    assert result.standalone_query == "介绍一下液压系统。"
