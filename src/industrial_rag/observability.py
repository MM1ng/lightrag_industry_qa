"""Sanitized per-request observability records (Phase 6)."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("industrial_rag.observability")

FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "workspace_endpoint",
    "system_prompt",
    "raw_document_body",
    "secret",
}


@dataclass(frozen=True, slots=True)
class TraceRecord:
    request_id: str
    trace_id: str
    kb_id: str | None = None
    generation: str | None = None
    document_scope: str | None = None
    query_mode: str = "mix"
    retrieval_count: int = 0
    retrieved_chunk_ids: tuple[str, ...] = ()
    retrieval_latency: float = 0.0
    embedding_latency: float = 0.0
    graph_latency: float = 0.0
    answer_latency: float = 0.0
    total_latency: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    requested_model: str | None = None
    actual_model: str | None = None
    fallback: bool = False
    refusal: bool = False
    refusal_reason: str | None = None
    safety_policy_id: str | None = None
    citation_audit_status: str | None = None
    error_code: str | None = None
    cache_hit: bool = False
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    extra: dict[str, Any] = field(default_factory=dict)

    def sanitized_dict(self, *, max_chunk_ids: int = 50) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_chunk_ids"] = list(self.retrieved_chunk_ids[:max_chunk_ids])
        data["retrieval_count"] = len(self.retrieved_chunk_ids)
        for key in list(data):
            if any(part in key.casefold() for part in FORBIDDEN_FIELDS):
                data.pop(key, None)
        return data


def emit_trace(record: TraceRecord, *, extra: dict[str, Any] | None = None) -> None:
    """Emit a sanitized request trace to the observability logger."""
    payload = record.sanitized_dict()
    if extra:
        payload.update(extra)
    logger.info("request trace", extra={"trace": payload})
