"""Best-effort trace persistence isolated from the ordinary query transaction."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from industrial_rag.config import Settings
from industrial_rag.db.session import get_trace_session_factory
from industrial_rag.operational_metrics import operational_metrics
from industrial_rag.repositories.retrieval_trace_repository import (
    RetrievalTraceRepository,
)
from industrial_rag.services.query_application_service import GenerationQueryResult

logger = logging.getLogger(__name__)


class RetrievalTraceService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def record_best_effort(
        self,
        *,
        request_id: str,
        trace_id: str,
        knowledge_base_id: str,
        execution: GenerationQueryResult,
        end_to_end_ms: float,
    ) -> None:
        trace = execution.result.retrieval_trace
        if trace is None:
            self._record_failure(request_id, trace_id, "MissingRetrievalTrace")
            return
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(
            seconds=self._settings.retrieval_trace_ttl_seconds
        )
        payload = {
            "request_id": request_id,
            "trace_id": trace_id,
            "knowledge_base_id": knowledge_base_id,
            "generation_id": execution.generation_id,
            "generation_epoch": execution.generation_epoch,
            **trace.to_payload(),
            "end_to_end_ms": end_to_end_ms,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        try:
            factory = get_trace_session_factory()
            async with factory() as session:
                try:
                    async with session.begin():
                        await RetrievalTraceRepository(session).create_immutable(
                            request_id=request_id,
                            trace_id=trace_id,
                            knowledge_base_id=knowledge_base_id,
                            generation_id=execution.generation_id,
                            trace_version=trace.trace_version,
                            payload=payload,
                            created_at=created_at,
                            expires_at=expires_at,
                        )
                except Exception:
                    await session.rollback()
                    raise
        except Exception as error:
            self._record_failure(request_id, trace_id, type(error).__name__)

    @staticmethod
    def _record_failure(request_id: str, trace_id: str, error_type: str) -> None:
        operational_metrics.increment("retrieval_trace_write_failure_total")
        logger.warning(
            "Retrieval trace write failed request_id=%s trace_id=%s error_type=%s",
            request_id,
            trace_id,
            error_type,
        )

    async def get_unexpired(self, request_id: str) -> dict | None:
        factory = get_trace_session_factory()
        async with factory() as session:
            record = await RetrievalTraceRepository(session).get_unexpired(
                request_id,
                now=datetime.now(UTC),
            )
            return None if record is None else dict(record.payload)
