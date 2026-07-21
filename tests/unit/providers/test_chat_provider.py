from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict

from industrial_energy_agent.providers.base import (
    ChatResponse,
    ProviderRequestError,
    ProviderResponseError,
    ToolCall,
)
from industrial_energy_agent.providers.fake import FakeChatProvider
from industrial_energy_agent.providers.openai_compatible import OpenAIChatProvider


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str


class _FakeCompletions:
    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None
        self.content: str | None = '{"intent":"sensor_query"}'
        self.tool_calls: list[Any] | None = None
        self.failures: list[Exception] = []
        self.call_count = 0
        self.empty_choices = False
        self.missing_content = False

    def create(self, **request: Any) -> SimpleNamespace:
        self.call_count += 1
        self.last_request = request
        if self.failures:
            raise self.failures.pop(0)
        message = (
            SimpleNamespace(tool_calls=self.tool_calls)
            if self.missing_content
            else SimpleNamespace(content=self.content, tool_calls=self.tool_calls)
        )
        return SimpleNamespace(
            id="chatcmpl-test",
            model="qwen3.7-plus",
            choices=(
                []
                if self.empty_choices
                else [SimpleNamespace(message=message, finish_reason="stop")]
            ),
        )


class _FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_json_mode_is_non_thinking_and_pydantic_validated() -> None:
    client = _FakeClient()
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")

    result = provider.complete_json("Return JSON intent", IntentDecision)

    assert result == IntentDecision(intent="sensor_query")
    assert client.chat.completions.last_request is not None
    assert client.chat.completions.last_request["response_format"] == {"type": "json_object"}
    assert client.chat.completions.last_request["extra_body"] == {"enable_thinking": False}
    assert "max_tokens" not in client.chat.completions.last_request


def test_json_mode_keeps_required_json_keyword_with_custom_prompts() -> None:
    client = _FakeClient()
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")

    provider.complete_json(
        "Classify the intent.",
        IntentDecision,
        system_prompt="Return a structured intent decision.",
    )

    assert client.chat.completions.last_request is not None
    messages = client.chat.completions.last_request["messages"]
    assert any("JSON" in message["content"] for message in messages)


def test_text_completion_returns_assistant_content() -> None:
    client = _FakeClient()
    client.chat.completions.content = "离心泵启动前应完成安全检查。"
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")

    result = provider.complete("离心泵启动前需要检查什么?")

    assert result == "离心泵启动前应完成安全检查。"
    assert client.chat.completions.last_request is not None
    assert client.chat.completions.last_request["model"] == "qwen3.7-plus"


def test_function_calling_returns_validated_arguments() -> None:
    client = _FakeClient()
    client.chat.completions.content = None
    client.chat.completions.tool_calls = [
        SimpleNamespace(
            id="call-1",
            type="function",
            function=SimpleNamespace(
                name="query_sensor_cycle",
                arguments='{"cycle_id":1200}',
            ),
        )
    ]
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "query_sensor_cycle",
                "description": "查询液压系统周期摘要",
                "parameters": {
                    "type": "object",
                    "properties": {"cycle_id": {"type": "integer"}},
                    "required": ["cycle_id"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    result = provider.complete_with_tools("查询第1200周期", tools)

    assert isinstance(result, ChatResponse)
    assert result.content is None
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "query_sensor_cycle"
    assert result.tool_calls[0].arguments == {"cycle_id": 1200}
    assert client.chat.completions.last_request is not None
    assert client.chat.completions.last_request["tools"] == tools
    assert client.chat.completions.last_request["tool_choice"] == "auto"


def test_chat_retries_a_transport_timeout_then_succeeds() -> None:
    client = _FakeClient()
    client.chat.completions.content = "ok"
    client.chat.completions.failures = [
        openai.APITimeoutError(
            request=httpx.Request("POST", "https://example.invalid/chat/completions")
        )
    ]
    provider = OpenAIChatProvider(
        client,
        model="qwen3.7-plus",
        max_retries=2,
        retry_base_delay_seconds=0,
    )

    assert provider.complete("health check") == "ok"
    assert client.chat.completions.call_count == 2


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(429, openai.RateLimitError), (500, openai.InternalServerError)],
)
def test_chat_retries_rate_limit_and_server_errors(
    status_code: int,
    error_type: type[openai.APIStatusError],
) -> None:
    client = _FakeClient()
    client.chat.completions.content = "ok"
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    client.chat.completions.failures = [
        error_type(
            "retryable",
            response=httpx.Response(status_code, request=request),
            body=None,
        )
    ]
    provider = OpenAIChatProvider(
        client,
        model="qwen3.7-plus",
        max_retries=1,
        retry_base_delay_seconds=0,
    )

    assert provider.complete("health check") == "ok"
    assert client.chat.completions.call_count == 2


def test_chat_does_not_retry_a_client_error() -> None:
    client = _FakeClient()
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    client.chat.completions.failures = [
        openai.BadRequestError(
            "invalid request",
            response=httpx.Response(400, request=request),
            body=None,
        )
    ]
    provider = OpenAIChatProvider(
        client,
        model="qwen3.7-plus",
        max_retries=2,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(ProviderRequestError) as captured:
        provider.complete("invalid")

    assert captured.value.retryable is False
    assert captured.value.status_code == 400
    assert client.chat.completions.call_count == 1


def test_exhausted_transport_retry_raises_vendor_neutral_error() -> None:
    client = _FakeClient()
    client.chat.completions.failures = [
        openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid/chat/completions")
        ),
        openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid/chat/completions")
        ),
    ]
    provider = OpenAIChatProvider(
        client,
        model="qwen3.7-plus",
        max_retries=1,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(ProviderRequestError) as captured:
        provider.complete("Authorization: Bearer secret")

    assert captured.value.retryable is True
    assert captured.value.status_code is None
    assert "secret" not in str(captured.value)
    assert client.chat.completions.call_count == 2


def test_invalid_json_result_is_rejected_without_retry() -> None:
    client = _FakeClient()
    client.chat.completions.content = '{"intent":"sensor_query","unexpected":true}'
    provider = OpenAIChatProvider(
        client,
        model="qwen3.7-plus",
        max_retries=2,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(ProviderResponseError, match="structured chat response"):
        provider.complete_json("Return JSON intent", IntentDecision)

    assert client.chat.completions.call_count == 1


def test_chat_rejects_an_empty_choices_response_with_stable_error() -> None:
    client = _FakeClient()
    client.chat.completions.empty_choices = True
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")

    with pytest.raises(ProviderResponseError, match="invalid chat response"):
        provider.complete("health check")


def test_chat_rejects_a_message_without_content_with_stable_error() -> None:
    client = _FakeClient()
    client.chat.completions.missing_content = True
    provider = OpenAIChatProvider(client, model="qwen3.7-plus")

    with pytest.raises(ProviderResponseError, match="invalid chat response"):
        provider.complete("health check")


def test_fake_chat_returns_configured_model_without_recording_prompt() -> None:
    secret_prompt = "Authorization: Bearer test-secret"
    expected = IntentDecision(intent="sensor_query")
    provider = FakeChatProvider(
        text_response="fake text",
        json_responses={IntentDecision: expected},
    )

    result = provider.complete_json(secret_prompt, IntentDecision)

    assert result == expected
    summary = provider.call_summaries[0].model_dump_json()
    assert "complete_json" in summary
    assert "IntentDecision" in summary
    assert secret_prompt not in summary
    assert "test-secret" not in summary


def test_fake_chat_returns_configured_text_without_recording_prompt() -> None:
    secret_prompt = "api_key=do-not-record"
    provider = FakeChatProvider(text_response="fake text")

    assert provider.complete(secret_prompt) == "fake text"
    summary = provider.call_summaries[0].model_dump_json()
    assert "complete" in summary
    assert secret_prompt not in summary
    assert "do-not-record" not in summary


def test_fake_chat_returns_configured_tool_decision() -> None:
    expected = ChatResponse(
        tool_calls=(
            ToolCall(
                id="fake-call-1",
                name="query_sensor_cycle",
                arguments={"cycle_id": 1200},
            ),
        ),
        finish_reason="tool_calls",
    )
    provider = FakeChatProvider(text_response="fake text", tool_response=expected)
    tools = [
        {
            "type": "function",
            "function": {"name": "query_sensor_cycle", "parameters": {}},
        }
    ]

    result = provider.complete_with_tools("secret sensor prompt", tools)

    assert result == expected
    assert provider.call_summaries[0].tool_names == ("query_sensor_cycle",)
    assert "secret sensor prompt" not in provider.call_summaries[0].model_dump_json()
