"""Shared public contracts and sanitized Trace helpers for agent tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from industrial_energy_agent.domain.errors import contains_sensitive_or_internal_text
from industrial_energy_agent.domain.models import TraceEvent, TraceScalar


class ToolModel(BaseModel):
    """Strict base class for every public tool input and output model."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


class ToolInputModel(ToolModel):
    """Shared strict input envelope with a safe caller correlation ID."""

    request_id: str = Field(
        default_factory=lambda: new_request_id(),
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )

    @field_validator("request_id")
    @classmethod
    def reject_unsafe_request_id(cls, value: str) -> str:
        if contains_sensitive_or_internal_text(value):
            raise ValueError("request_id contains prohibited content")
        return value


class ToolError(ToolModel):
    """Sanitized error envelope returned by all tools."""

    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ToolFailure(ToolModel):
    """Common fields carried by module-specific failure unions."""

    ok: Literal[False] = False
    error: ToolError
    trace: TraceEvent


def new_request_id() -> str:
    """Create an opaque request ID without embedding user or dependency input."""

    return f"tool-{uuid4().hex}"


def started_at() -> float:
    return perf_counter()


def make_trace(
    *,
    request_id: str,
    tool: str,
    started: float,
    status: Literal["success", "failure"],
    evidence_count: int = 0,
    parameter_summary: dict[str, TraceScalar] | None = None,
    error_code: str | None = None,
) -> TraceEvent:
    """Build the bounded domain Trace; raw inputs and exception text are never accepted."""

    return TraceEvent(
        request_id=request_id,
        node="tool",
        action=tool,
        status=status,
        duration_ms=max(0.0, (perf_counter() - started) * 1_000),
        tool=tool,
        evidence_count=evidence_count,
        parameter_summary=parameter_summary or {},
        error_code=error_code,
    )


def make_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, JsonValue] | None = None,
) -> ToolError:
    """Create a public error only from fixed caller-owned text, never exception text."""

    return ToolError(
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    )


def dump_result(result: BaseModel) -> dict[str, Any]:
    """Return JSON-compatible dictionaries expected by LangChain tool callers."""

    return result.model_dump(mode="json")


def invalid_input_result(
    tool: str, *, tool_call_id: str | None = None
) -> dict[str, Any] | ToolMessage:
    """Return a fresh, sanitized failure when LangChain schema validation fails."""

    request_id = new_request_id()
    started = started_at()
    failure = dump_result(
        ToolFailure(
            error=make_error("INVALID_INPUT", "工具输入不符合结构化参数要求。"),
            trace=make_trace(
                request_id=request_id,
                tool=tool,
                started=started,
                status="failure",
                parameter_summary={"schema_valid": False},
                error_code="INVALID_INPUT",
            ),
        )
    )
    if tool_call_id is None:
        return failure
    return ToolMessage(
        content=json.dumps(failure, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=tool,
        status="error",
    )


class SafeStructuredTool(StructuredTool):
    """StructuredTool boundary that converts schema errors to typed failures."""

    def run(
        self,
        tool_input: str | dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        tool_call_id = kwargs.get("tool_call_id")
        safe_tool_call_id = tool_call_id if isinstance(tool_call_id, str) else None
        try:
            self._parse_input(tool_input, safe_tool_call_id)
        except (ValidationError, ValueError, TypeError):
            return invalid_input_result(self.name, tool_call_id=safe_tool_call_id)
        return super().run(tool_input, *args, **kwargs)

    async def arun(
        self,
        tool_input: str | dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        tool_call_id = kwargs.get("tool_call_id")
        safe_tool_call_id = tool_call_id if isinstance(tool_call_id, str) else None
        try:
            self._parse_input(tool_input, safe_tool_call_id)
        except (ValidationError, ValueError, TypeError):
            return invalid_input_result(self.name, tool_call_id=safe_tool_call_id)
        return await super().arun(tool_input, *args, **kwargs)


def build_safe_structured_tool(
    *,
    func: Callable[..., Any],
    name: str,
    description: str,
    args_schema: type[BaseModel],
) -> SafeStructuredTool:
    """Construct the uniform sync/async-safe LangChain boundary."""

    tool = SafeStructuredTool.from_function(
        func=func,
        name=name,
        description=description,
        args_schema=args_schema,
    )
    if not isinstance(tool, SafeStructuredTool):
        raise TypeError("safe tool factory returned an unexpected tool type")
    return tool
