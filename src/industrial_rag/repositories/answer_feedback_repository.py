"""Persistence helpers for answer snapshots and feedback labels."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import AnswerFeedbackRecord


class AnswerFeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_request_id(self, request_id: str) -> AnswerFeedbackRecord | None:
        result = await self._session.execute(
            select(AnswerFeedbackRecord).where(
                AnswerFeedbackRecord.request_id == request_id
            )
        )
        return result.scalar_one_or_none()

    async def create_or_get_by_request_id(
        self,
        *,
        request_id: str,
        trace_id: str | None,
        generation_id: str | None,
        knowledge_base_id: str | None,
        question: str,
        answer: str,
        answer_status: str,
        citations: list[dict],
        retrieved_chunks: list[dict],
    ) -> AnswerFeedbackRecord:
        existing = await self.get_by_request_id(request_id)
        if existing is not None:
            return existing

        record = AnswerFeedbackRecord(
            request_id=request_id,
            trace_id=trace_id,
            generation_id=generation_id,
            knowledge_base_id=knowledge_base_id,
            question=question,
            answer=answer,
            answer_status=answer_status,
            citations=citations,
            retrieved_chunks=retrieved_chunks,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def update_feedback_by_request_id(
        self,
        *,
        request_id: str,
        feedback_type: str,
        feedback_reason: str | None,
        feedback_comment: str | None,
    ) -> AnswerFeedbackRecord | None:
        record = await self.get_by_request_id(request_id)
        if record is None:
            return None
        record.feedback_type = feedback_type
        record.feedback_reason = feedback_reason
        record.feedback_comment = feedback_comment
        record.updated_at = datetime.now(UTC)
        await self._session.flush()
        return record

    async def update_review_by_id(
        self,
        *,
        record_id: str,
        values: dict[str, str | None],
    ) -> AnswerFeedbackRecord | None:
        result = await self._session.execute(
            select(AnswerFeedbackRecord).where(AnswerFeedbackRecord.id == record_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(UTC)
        await self._session.flush()
        return record

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        feedback_type: str | None = None,
        feedback_reason: str | None = None,
        knowledge_base_id: str | None = None,
        generation_id: str | None = None,
        answer_status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[AnswerFeedbackRecord], int]:
        filters = _filters(
            feedback_type=feedback_type,
            feedback_reason=feedback_reason,
            knowledge_base_id=knowledge_base_id,
            generation_id=generation_id,
            answer_status=answer_status,
            created_from=created_from,
            created_to=created_to,
        )
        query: Select = select(AnswerFeedbackRecord).where(*filters).order_by(
            AnswerFeedbackRecord.created_at.desc()
        )
        rows = (
            await self._session.execute(query.offset(offset).limit(limit))
        ).scalars().all()
        total = int(
            (
                await self._session.execute(
                    select(func.count(AnswerFeedbackRecord.id)).where(*filters)
                )
            ).scalar_one()
        )
        return rows, total

    async def list_all(self) -> list[AnswerFeedbackRecord]:
        result = await self._session.execute(
            select(AnswerFeedbackRecord).order_by(
                AnswerFeedbackRecord.created_at.desc()
            )
        )
        return list(result.scalars().all())


def _filters(
    *,
    feedback_type: str | None,
    feedback_reason: str | None,
    knowledge_base_id: str | None,
    generation_id: str | None,
    answer_status: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list:
    filters = []
    if feedback_type is not None:
        filters.append(AnswerFeedbackRecord.feedback_type == feedback_type)
    if feedback_reason is not None:
        filters.append(AnswerFeedbackRecord.feedback_reason == feedback_reason)
    if knowledge_base_id is not None:
        filters.append(AnswerFeedbackRecord.knowledge_base_id == knowledge_base_id)
    if generation_id is not None:
        filters.append(AnswerFeedbackRecord.generation_id == generation_id)
    if answer_status is not None:
        filters.append(AnswerFeedbackRecord.answer_status == answer_status)
    if created_from is not None:
        filters.append(AnswerFeedbackRecord.created_at >= created_from)
    if created_to is not None:
        filters.append(AnswerFeedbackRecord.created_at <= created_to)
    return filters
