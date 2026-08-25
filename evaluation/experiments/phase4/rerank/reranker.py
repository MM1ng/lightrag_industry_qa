"""Provider-neutral Reranker interface with an exact-model gate.

Production default stays disabled. A concrete provider may only be used when
``RERANK_MODEL`` is an exact model name (latest aliases rejected) and
``RERANK_FALLBACK_ENABLED=false``.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .config import RERANK_CONFIG


class RerankConfigurationError(RuntimeError):
    pass


ALLOWED_RERANK_MODELS = frozenset({"qwen3-rerank"})
_ALIAS_WORDS = frozenset({"latest", "auto", "default"})


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    chunk_id: str
    original_rank: int
    original_score: float | None
    rerank_rank: int
    rerank_score: float | None
    document_id: str
    page: int | None
    text_hash: str
    model: str
    latency: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "original_rank": self.original_rank,
            "original_score": self.original_score,
            "rerank_rank": self.rerank_rank,
            "rerank_score": self.rerank_score,
            "document_id": self.document_id,
            "page": self.page,
            "text_hash": self.text_hash,
            "model": self.model,
            "latency": self.latency,
            "status": self.status,
        }


class RerankerProvider(Protocol):
    model: str

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int,
        metadata: dict[str, Any] | None = None,
    ) -> list[RerankedCandidate]: ...


def resolve_rerank_model(env: dict[str, str] | None = None) -> str | None:
    """Return the allowlisted exact model or None (never picks a model)."""
    env = dict(os.environ if env is None else env)
    model = (env.get("RERANK_MODEL") or "").strip()
    if not model:
        return None
    if model.casefold() in _ALIAS_WORDS or model.endswith("-latest"):
        raise RerankConfigurationError(
            f"RERANK_MODEL must be an exact model name, got alias: {model!r}"
        )
    if model not in ALLOWED_RERANK_MODELS:
        raise RerankConfigurationError(
            f"RERANK_MODEL {model!r} is not in the allowed rerank model allowlist "
            f"{sorted(ALLOWED_RERANK_MODELS)}"
        )
    return model


def rerank_gate(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Preflight gate: exact model present, fallback disabled."""
    env = dict(os.environ if env is None else env)
    checks = {
        "model_configured": bool(env.get("RERANK_MODEL", "").strip()),
        "fallback_disabled": (
            env.get("RERANK_FALLBACK_ENABLED", "false").strip().lower() != "true"
        ),
        "exact_model": True,
    }
    model = resolve_rerank_model(env)
    checks["exact_model"] = model is not None
    return {
        "allowed": all(checks.values()) and model is not None,
        "model": model,
        "checks": checks,
    }


def cache_key(query: str, candidates: list[dict[str, Any]], model: str) -> str:
    """Exact-match cache key: model + query hash + ordered candidate ids/hashes."""
    payload = "\x00".join(
        [
            model,
            hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "|".join(str(c.get("chunk_id")) for c in candidates),
            hashlib.sha256(
                "|".join(str(c.get("child_text_hash", "")) for c in candidates).encode("utf-8")
            ).hexdigest(),
            str(RERANK_CONFIG["candidate_k"]),
            str(RERANK_CONFIG["final_k"]),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BlockedReranker:
    """Used when no exact RERANK_MODEL is configured; fails loudly."""

    model: str | None = None

    def __init__(self, reason: str = "RERANK_MODEL is not configured") -> None:
        self.reason = reason

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int,
        metadata: dict[str, Any] | None = None,
    ) -> list[RerankedCandidate]:
        raise RerankConfigurationError(self.reason)
