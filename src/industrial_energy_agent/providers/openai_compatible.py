"""OpenAI-compatible provider implementations for BaiLian model endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from math import isfinite
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from industrial_energy_agent.providers.base import (
    ChatResponse,
    ProviderRequestError,
    ProviderResponseError,
    ToolCall,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
_TOOL_ARGUMENTS_ADAPTER = TypeAdapter(dict[str, JsonValue])
_EMBEDDING_BATCH_SIZE = 10


def _is_retryable_openai_error(error: openai.APIError) -> bool:
    if isinstance(error, openai.APIConnectionError):
        return True
    return isinstance(error, openai.APIStatusError) and (
        error.status_code == 429 or error.status_code >= 500
    )


def _request_with_retry(
    operation: Callable[[], Any],
    *,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> Any:
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except openai.APIError as error:
            retryable = _is_retryable_openai_error(error)
            if retryable and attempt < max_retries:
                time.sleep(retry_base_delay_seconds * (2**attempt))
                continue
            status_code = error.status_code if isinstance(error, openai.APIStatusError) else None
            raise ProviderRequestError(
                retryable=retryable,
                status_code=status_code,
            ) from error
    raise RuntimeError("unreachable retry state")


def _first_chat_choice(response: Any) -> Any:
    try:
        choice = response.choices[0]
        message = choice.message
    except (AttributeError, IndexError, TypeError) as error:
        raise ProviderResponseError("Provider returned an invalid chat response") from error
    if message is None or not hasattr(message, "content"):
        raise ProviderResponseError("Provider returned an invalid chat response")
    return choice


class OpenAIChatProvider:
    """Dependency-injected synchronous chat provider."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 0.25,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be non-negative")
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def _request(self, operation: Callable[[], Any]) -> Any:
        return _request_with_retry(
            operation,
            max_retries=self._max_retries,
            retry_base_delay_seconds=self._retry_base_delay_seconds,
        )

    def complete_json(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        system_prompt: str = "Return valid JSON only.",
    ) -> ModelT:
        """Return a JSON Mode response validated by the requested Pydantic model."""

        if "JSON" not in system_prompt and "JSON" not in prompt:
            system_prompt = f"{system_prompt.rstrip()} Return valid JSON only."
        response = self._request(
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                extra_body={"enable_thinking": False},
                timeout=self._timeout_seconds,
            )
        )
        content = _first_chat_choice(response).message.content
        if not isinstance(content, str):
            raise ProviderResponseError("Provider returned an invalid chat response")
        try:
            return response_model.model_validate_json(content)
        except ValidationError as error:
            raise ProviderResponseError(
                "Provider returned an invalid structured chat response"
            ) from error

    def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Return assistant text for a single user prompt."""

        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = self._request(
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                extra_body={"enable_thinking": False},
                timeout=self._timeout_seconds,
            )
        )
        content = _first_chat_choice(response).message.content
        if not isinstance(content, str):
            raise ProviderResponseError("Provider returned an invalid chat response")
        return content

    def complete_with_tools(
        self,
        prompt: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> ChatResponse:
        """Return normalized function-call decisions from the model."""

        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = self._request(
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=list(tools),
                tool_choice="auto",
                extra_body={"enable_thinking": False},
                timeout=self._timeout_seconds,
            )
        )
        choice = _first_chat_choice(response)
        try:
            normalized_calls = tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=_TOOL_ARGUMENTS_ADAPTER.validate_json(call.function.arguments),
                )
                for call in (choice.message.tool_calls or [])
            )
            return ChatResponse(
                content=choice.message.content,
                tool_calls=normalized_calls,
                finish_reason=choice.finish_reason,
            )
        except (AttributeError, TypeError, ValidationError) as error:
            raise ProviderResponseError(
                "Provider returned an invalid structured chat response"
            ) from error


class OpenAIEmbeddingProvider:
    """Dependency-injected synchronous embedding provider."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        dimensions: int = 1024,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 0.25,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be non-negative")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def _request(self, operation: Callable[[], Any]) -> Any:
        return _request_with_retry(
            operation,
            max_retries=self._max_retries,
            retry_base_delay_seconds=self._retry_base_delay_seconds,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed text with BaiLian's confirmed plural ``dimensions`` parameter."""

        vectors: list[list[float]] = []
        for offset in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
            batch = list(texts[offset : offset + _EMBEDDING_BATCH_SIZE])
            response = self._request(
                partial(
                    self._client.embeddings.create,
                    model=self._model,
                    input=batch,
                    dimensions=self._dimensions,
                    encoding_format="float",
                    timeout=self._timeout_seconds,
                )
            )
            items = sorted(response.data, key=lambda value: value.index)
            if [item.index for item in items] != list(range(len(batch))):
                raise ProviderResponseError(
                    "Embedding response has an unexpected item count or indices"
                )
            for item in items:
                vector = [float(value) for value in item.embedding]
                if len(vector) != self._dimensions:
                    raise ProviderResponseError("Embedding response has an unexpected dimension")
                if not all(isfinite(value) for value in vector):
                    raise ProviderResponseError("Embedding response contains non-finite values")
                vectors.append(vector)
        return vectors
