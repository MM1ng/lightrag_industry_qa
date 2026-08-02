"""Authoritative production QA strategy (Phase 6).

One source of truth for the frozen release-candidate strategy. Defaults match
``evaluation/experiments/phase6/frozen_strategy.json``. Environment overrides
are explicit; while locked, any deviation from the frozen core strategy fails
startup instead of silently changing behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

from dotenv import load_dotenv

from industrial_rag.config import PROJECT_ROOT

FROZEN_STRATEGY = {
    "parser_pipeline": "pymupdf_standard_adapter",
    "query_mode": "mix",
    "top_k": 12,
    "chunk_top_k": 20,
    "parent_expansion_enabled": False,
    "rerank_enabled": False,
    "context_strategy": "current_rows",
    "answer_strategy": "current",
    "answer_model": "qwen-plus-2025-07-28",
    "embedding_model": "text-embedding-v4",
    "embedding_dimension": 1024,
    "fallback_enabled": False,
    "thinking_enabled": False,
}

_ENV_NAMES = {
    "parser_pipeline": "QA_PARSER_PIPELINE",
    "query_mode": "QA_QUERY_MODE",
    "top_k": "QA_TOP_K",
    "chunk_top_k": "QA_CHUNK_TOP_K",
    "parent_expansion_enabled": "QA_PARENT_EXPANSION_ENABLED",
    "rerank_enabled": "QA_RERANK_ENABLED",
    "context_strategy": "QA_CONTEXT_STRATEGY",
    "answer_strategy": "QA_ANSWER_STRATEGY",
    "answer_model": "QA_ANSWER_MODEL",
    "embedding_model": "QA_EMBEDDING_MODEL",
    "embedding_dimension": "QA_EMBEDDING_DIMENSION",
    "fallback_enabled": "QA_FALLBACK_ENABLED",
    "thinking_enabled": "QA_THINKING_ENABLED",
    "request_timeout_seconds": "QA_REQUEST_TIMEOUT_SECONDS",
    "max_retries": "QA_MAX_RETRIES",
    "citation_shadow_audit_enabled": "CITATION_SHADOW_AUDIT_ENABLED",
    "safety_policy_enabled": "QA_SAFETY_POLICY_ENABLED",
    "observability_enabled": "QA_OBSERVABILITY_ENABLED",
    "locked": "QA_LOCKED",
}


class ProductionConfigError(RuntimeError):
    """Raised when the production QA configuration is illegal."""


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ProductionConfigError(f"{name} must be a boolean, got {value!r}")


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ProductionConfigError(f"{name} must be an integer, got {value!r}") from error


def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ProductionConfigError(f"{name} must be a number, got {value!r}") from error


@dataclass(frozen=True, slots=True)
class ProductionQASettings:
    """Validated release-candidate QA settings (no secrets)."""

    parser_pipeline: str = "pymupdf_standard_adapter"
    query_mode: str = "mix"
    top_k: int = 12
    chunk_top_k: int = 20
    parent_expansion_enabled: bool = False
    rerank_enabled: bool = False
    context_strategy: str = "current_rows"
    answer_strategy: str = "current"
    answer_model: str = "qwen-plus-2025-07-28"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    fallback_enabled: bool = False
    thinking_enabled: bool = False
    request_timeout_seconds: float = 180.0
    max_retries: int = 2
    citation_shadow_audit_enabled: bool = False
    safety_policy_enabled: bool = True
    observability_enabled: bool = True
    locked: bool = True

    def __post_init__(self) -> None:
        if self.parser_pipeline != "pymupdf_standard_adapter":
            raise ProductionConfigError(
                f"parser_pipeline must be pymupdf_standard_adapter, got {self.parser_pipeline!r}"
            )
        if self.query_mode != "mix":
            raise ProductionConfigError(f"query_mode must be mix, got {self.query_mode!r}")
        if self.top_k <= 0:
            raise ProductionConfigError("top_k must be positive")
        if self.chunk_top_k < self.top_k:
            raise ProductionConfigError("chunk_top_k must be >= top_k")
        if self.context_strategy != "current_rows":
            raise ProductionConfigError(
                f"context_strategy must be current_rows, got {self.context_strategy!r}"
            )
        if self.answer_strategy != "current":
            raise ProductionConfigError(
                f"answer_strategy must be current, got {self.answer_strategy!r}"
            )
        if self.embedding_model != "text-embedding-v4":
            raise ProductionConfigError(
                f"embedding_model must be text-embedding-v4, got {self.embedding_model!r}"
            )
        if self.embedding_dimension != 1024:
            raise ProductionConfigError(
                f"embedding_dimension must be 1024, got {self.embedding_dimension!r}"
            )
        if self.request_timeout_seconds <= 0:
            raise ProductionConfigError("request_timeout_seconds must be positive")
        if not 0 <= self.max_retries <= 5:
            raise ProductionConfigError("max_retries must be in [0, 5]")
        if self.locked:
            deviations = [
                key
                for key, expected in FROZEN_STRATEGY.items()
                if getattr(self, key) != expected
            ]
            if deviations:
                raise ProductionConfigError(
                    f"locked production strategy deviates from frozen defaults: {deviations}"
                )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        reject_unknown: bool = False,
    ) -> ProductionQASettings:
        allowed = {field.name for field in fields(cls)}
        if reject_unknown:
            unknown = set(values) - allowed
            if unknown:
                raise ProductionConfigError(f"unknown production config keys: {sorted(unknown)}")
        data: dict[str, Any] = {}
        for key, env_name in _ENV_NAMES.items():
            if env_name not in values:
                continue
            value = values[env_name]
            if key in {
                "top_k",
                "chunk_top_k",
                "embedding_dimension",
                "max_retries",
            }:
                data[key] = _as_int(value, env_name)
            elif key in {"request_timeout_seconds"}:
                data[key] = _as_float(value, env_name)
            elif key in {
                "parent_expansion_enabled",
                "rerank_enabled",
                "fallback_enabled",
                "thinking_enabled",
                "citation_shadow_audit_enabled",
                "safety_policy_enabled",
                "observability_enabled",
                "locked",
            }:
                data[key] = _as_bool(value, env_name)
            else:
                data[key] = str(value).strip()
        return cls(**data)

    @classmethod
    def from_env(cls) -> ProductionQASettings:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls.from_mapping(os.environ)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sanitized_summary(self) -> dict[str, Any]:
        """Startup-safe summary (this config holds no secrets)."""
        return asdict(self)

    def strategy_hash(self) -> str:
        payload = {
            key: self.to_dict()[key]
            for key in FROZEN_STRATEGY
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
