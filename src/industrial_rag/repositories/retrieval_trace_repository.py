"""Persistence operations for immutable, TTL-bounded retrieval traces."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import RetrievalTraceRecord


class RetrievalTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_immutable(
        self,
        *,
        request_id: str,
        trace_id: str,
        knowledge_base_id: str,
        generation_id: str,
        trace_version: str,
        payload: dict,
        created_at: datetime,
        expires_at: datetime,
    ) -> RetrievalTraceRecord:
        record = RetrievalTraceRecord(
            request_id=request_id,
            trace_id=trace_id,
            knowledge_base_id=knowledge_base_id,
            generation_id=generation_id,
            trace_version=trace_version,
            payload=payload,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_unexpired(
        self, request_id: str, *, now: datetime
    ) -> RetrievalTraceRecord | None:
        result = await self._session.execute(
            select(RetrievalTraceRecord).where(
                RetrievalTraceRecord.request_id == request_id,
                RetrievalTraceRecord.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def delete_expired(self, *, now: datetime) -> int:
        result = await self._session.execute(
            delete(RetrievalTraceRecord).where(RetrievalTraceRecord.expires_at <= now)
        )
        return int(result.rowcount or 0)
