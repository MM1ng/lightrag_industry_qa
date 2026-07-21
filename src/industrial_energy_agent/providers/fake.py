"""Deterministic offline providers for tests and local workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any, TypeVar

from pydantic import BaseModel

from industrial_energy_agent.providers.base import (
    ChatResponse,
    ProviderCallSummary,
    ProviderResponseError,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeChatProvider:
    """Return configured responses without retaining prompts."""

    def __init__(
        self,
        *,
        text_response: str,
        json_responses: Mapping[type[BaseModel], BaseModel] | None = None,
        tool_response: ChatResponse | None = None,
    ) -> None:
        self._text_response = text_response
        self._json_responses = dict(json_responses or {})
        self._tool_response = tool_response
        self._call_summaries: list[ProviderCallSummary] = []

    @property
    def call_summaries(self) -> tuple[ProviderCallSummary, ...]:
        return tuple(self._call_summaries)

    def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        del prompt, system_prompt
        self._call_summaries.append(ProviderCallSummary(operation="complete"))
        return self._text_response

    def complete_json(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        system_prompt: str = "Return valid JSON only.",
    ) -> ModelT:
        del prompt, system_prompt
        self._call_summaries.append(
            ProviderCallSummary(
                operation="complete_json",
                response_schema=response_model.__name__,
            )
        )
        configured = self._json_responses.get(response_model)
        if configured is None:
            raise ProviderResponseError(
                f"No fake JSON response configured for {response_model.__name__}"
            )
        return response_model.model_validate(configured.model_dump())

    def complete_with_tools(
        self,
        prompt: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> ChatResponse:
        del prompt, system_prompt
        tool_names: list[str] = []
        for tool in tools:
            function = tool.get("function")
            if isinstance(function, Mapping):
                name = function.get("name")
                if isinstance(name, str):
                    tool_names.append(name)
        self._call_summaries.append(
            ProviderCallSummary(
                operation="complete_with_tools",
                tool_names=tuple(tool_names),
            )
        )
        if self._tool_response is None:
            raise ProviderResponseError("No fake tool response configured")
        return self._tool_response.model_copy(deep=True)


class FakeEmbeddingProvider:
    """Return configured vectors while recording only input cardinality."""

    def __init__(self, *, vectors: list[list[float]], dimensions: int = 1024) -> None:
        self._vectors = [list(vector) for vector in vectors]
        self._dimensions = dimensions
        self._call_summaries: list[ProviderCallSummary] = []

    @property
    def call_summaries(self) -> tuple[ProviderCallSummary, ...]:
        return tuple(self._call_summaries)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        input_count = len(texts)
        self._call_summaries.append(ProviderCallSummary(operation="embed", input_count=input_count))
        if len(self._vectors) != input_count:
            raise ProviderResponseError("Fake embedding count does not match the input count")
        if any(len(vector) != self._dimensions for vector in self._vectors):
            raise ProviderResponseError("Fake embedding has an unexpected dimension")
        if any(not all(isfinite(value) for value in vector) for vector in self._vectors):
            raise ProviderResponseError("Fake embedding contains non-finite values")
        return [list(vector) for vector in self._vectors]
