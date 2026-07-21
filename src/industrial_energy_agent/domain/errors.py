"""Public error contracts and domain-specific validation errors."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

PUBLIC_INTERNAL_ERROR_MESSAGE = "请求处理失败\N{FULLWIDTH COMMA}请稍后重试。"

_UNSAFE_MESSAGE_PATTERNS = (
    re.compile(r"traceback", re.IGNORECASE),
    re.compile(r"\bfile\s+[\"']", re.IGNORECASE),
    re.compile(r"\b[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[^\\\s]+\\[^\\\s]+"),
    re.compile(r"(?<![:\w])/(?:[^/\s]+/)+[^/\s]+"),
    re.compile(r"/(?:Users|home|var|etc|tmp|opt|srv|workspace|app)/", re.IGNORECASE),
    re.compile(
        r"(?:DASHSCOPE_API_KEY|LLM_API_KEY|LIGHTRAG_API_KEY|SERVICE_TOKEN|"
        r"LANGFUSE_(?:PUBLIC|SECRET)_KEY|X[-_]API[-_]KEY|AUTHORIZATION|"
        r"PASSWORD|PASSWD|PRIVATE_KEY|SECRET|ACCESS_TOKEN|REFRESH_TOKEN)\s*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"https?://[^\s/]+\.cn-beijing\.maas\.aliyuncs\.com(?:/\S*)?", re.IGNORECASE),
    re.compile(r"https?://[^\s/:@]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"[\r\n]"),
)

_SAFE_TOKEN_METRIC_KEYS = {
    "token_count",
    "input_token_count",
    "output_token_count",
    "total_token_count",
    "max_tokens",
    "token_limit",
}


def normalize_field_name(name: str) -> str:
    """Normalize structured field names before applying security policy."""

    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def is_sensitive_field_name(name: str) -> bool:
    """Return whether a structured field name may carry credential material."""

    normalized = normalize_field_name(name)
    if normalized in _SAFE_TOKEN_METRIC_KEYS or normalized.endswith("_token_count"):
        return False
    sensitive_parts = {
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
    }
    parts = set(normalized.split("_"))
    return (
        bool(parts & sensitive_parts)
        or normalized in {"api_key", "apikey", "private_key", "x_api_key"}
        or normalized.endswith("_api_key")
        or normalized.endswith("_private_key")
        or normalized.startswith("authorization_")
    )


def contains_sensitive_or_internal_text(value: str) -> bool:
    """Detect text that must never cross a public error or Trace boundary."""

    return any(pattern.search(value) for pattern in _UNSAFE_MESSAGE_PATTERNS)


class CitationValidationError(ValueError):
    """Raised when an untrusted or source-inconsistent citation is formatted."""


class DomainValidationError(ValueError):
    """Raised when a repository operation violates a domain lifecycle rule."""


def sanitize_public_error_message(message: str) -> str:
    """Replace messages carrying stack, path, or credential markers."""

    if contains_sensitive_or_internal_text(message):
        return PUBLIC_INTERNAL_ERROR_MESSAGE
    return message


class StructuredError(BaseModel):
    """The complete error shape allowed to cross the public API boundary."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("message")
    @classmethod
    def keep_message_public(cls, value: str) -> str:
        """Guarantee accepted instances contain no internal diagnostic detail."""

        return sanitize_public_error_message(value)
