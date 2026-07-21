"""Logging safety helpers.

The redactor deliberately returns a new object so callers can retain an unmodified
event for non-logging control flow without risking accidental secret restoration.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretBytes, SecretStr

REDACTED = "***"

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "proxy_authorization",
    "secret",
    "set_cookie",
    "token",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)
_WORKSPACE_URL = re.compile(
    r"(?P<scheme>https?://)[^\s./:@]+"
    r"(?P<suffix>\.cn-beijing\.maas\.aliyuncs\.com(?::\d+)?(?:/[^\s\"'<>]*)?)",
    flags=re.IGNORECASE,
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _mask_workspace_url(value: str) -> str:
    return _WORKSPACE_URL.sub(
        lambda match: f"{match.group('scheme')}***{match.group('suffix')}",
        value,
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, (SecretStr, SecretBytes)):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return _mask_workspace_url(value)
    return value


def redact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively sanitized copy of a structured log event."""

    return {
        key: REDACTED if _is_sensitive_key(key) else _redact_value(value)
        for key, value in event.items()
    }
