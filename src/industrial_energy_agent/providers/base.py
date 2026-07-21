"""Vendor-neutral provider contracts and validated response models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ModelT = TypeVar("ModelT", bound=BaseModel)


class ProviderResponseError(RuntimeError):
    """Raised when a model endpoint returns an invalid response contract."""


class ProviderRequestError(RuntimeError):
    """Vendor-neutral model request failure without upstream message leakage."""

    def __init__(self, *, retryable: bool, status_code: int | None) -> None:
        super().__init__("Provider request failed")
        self.retryable = retryable
        self.status_code = status_code


class ProviderCallSummary(BaseModel):
    """Prompt-free metadata safe to expose in deterministic traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(min_length=1)
    input_count: int = Field(default=1, ge=0)
    response_schema: str | None = None
    tool_names: tuple[str, ...] = ()


class ToolCall(BaseModel):
    """A model-requested function call with decoded JSON arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    arguments: dict[str, JsonValue]


class ChatResponse(BaseModel):
    """Normalized assistant response for tool-calling requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None


class ChatProvider(Protocol):
    """Vendor-neutral synchronous chat boundary."""

    def complete(self, prompt: str, *, system_prompt: str | None = None) -> str: ...

    def complete_json(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        system_prompt: str = "Return valid JSON only.",
    ) -> ModelT: ...

    def complete_with_tools(
        self,
        prompt: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> ChatResponse: ...


class EmbeddingProvider(Protocol):
    """Vendor-neutral synchronous embedding boundary."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
