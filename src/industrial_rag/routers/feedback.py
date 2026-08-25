"""Minimal answer-quality feedback and evaluation sample APIs."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.models import AnswerFeedbackRecord
from industrial_rag.db.session import get_session
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.services.answer_feedback_service import (
    AnswerFeedbackService,
    FeedbackNotFoundError,
    FeedbackValidationError,
)

router = APIRouter(prefix="/v1", tags=["feedback"])
compat_router = APIRouter(prefix="/api", tags=["feedback"])

FeedbackType = Literal["helpful", "unhelpful"]
FeedbackReason = Literal[
    "answer_incorrect",
    "citation_unsupported",
    "answer_incomplete",
    "answer_not_found",
    "false_refusal",
    "unsafe_or_unnecessary_answer",
    "response_too_slow",
    "other",
]


class FeedbackSubmission(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    feedback_type: FeedbackType
    feedback_reason: FeedbackReason | None = None
    feedback_comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_reason(self) -> FeedbackSubmission:
        if self.feedback_type == "unhelpful" and self.feedback_reason is None:
            raise ValueError("unhelpful feedback_reason 必填")
        return self


class FeedbackRecordResponse(BaseModel):
    id: str
    request_id: str
    trace_id: str | None
    generation_id: str | None
    knowledge_base_id: str | None
    question: str
    answer: str
    answer_status: str
    feedback_type: str | None
    feedback_reason: str | None
    feedback_comment: str | None
    citations: list[dict]
    retrieved_chunks: list[dict]
    created_at: datetime
    updated_at: datetime
    review_result: dict[str, str | None]


class FeedbackListResponse(BaseModel):
    items: list[FeedbackRecordResponse]
    total: int
    offset: int
    limit: int


ReviewValue = Literal["true", "false", "unknown", "not_applicable"]
RootCause = Literal[
    "retrieval_failure",
    "rerank_failure",
    "answer_generation_failure",
    "citation_failure",
    "refusal_failure",
    "knowledge_gap",
    "question_unclear",
    "unknown",
]


class FeedbackReviewSubmission(BaseModel):
    answer_correct: ReviewValue | None = None
    answer_complete: ReviewValue | None = None
    citation_supported: ReviewValue | None = None
    refusal_appropriate: ReviewValue | None = None
    root_cause: RootCause | None = None
    review_notes: str | None = Field(default=None, max_length=2000)


def _record_response(record: AnswerFeedbackRecord) -> FeedbackRecordResponse:
    return FeedbackRecordResponse(
        id=record.id,
        request_id=record.request_id,
        trace_id=record.trace_id,
        generation_id=record.generation_id,
        knowledge_base_id=record.knowledge_base_id,
        question=record.question,
        answer=record.answer,
        answer_status=record.answer_status,
        feedback_type=record.feedback_type,
        feedback_reason=record.feedback_reason,
        feedback_comment=record.feedback_comment,
        citations=list(record.citations or []),
        retrieved_chunks=list(record.retrieved_chunks or []),
        created_at=record.created_at,
        updated_at=record.updated_at,
        review_result={
            "answer_correct": record.answer_correct,
            "answer_complete": record.answer_complete,
            "citation_supported": record.citation_supported,
            "refusal_appropriate": record.refusal_appropriate,
            "root_cause": record.root_cause,
            "review_notes": record.review_notes,
        },
    )


@router.post("/feedback", response_model=FeedbackRecordResponse)
async def submit_feedback(
    payload: FeedbackSubmission,
    session: AsyncSession = Depends(get_session),
) -> FeedbackRecordResponse:
    try:
        record = await AnswerFeedbackService(session).submit_feedback(
            request_id=payload.request_id,
            feedback_type=payload.feedback_type,
            feedback_reason=payload.feedback_reason,
            feedback_comment=payload.feedback_comment,
        )
    except FeedbackNotFoundError as error:
        raise AppError(
            AppErrorCode.feedback_not_found,
            "该请求没有可反馈的业务回答。",
            status_code=404,
        ) from error
    except FeedbackValidationError as error:
        raise AppError(str(error), str(error), status_code=422) from error
    return _record_response(record)


@compat_router.post("/feedback", response_model=FeedbackRecordResponse)
async def submit_feedback_compat(
    payload: FeedbackSubmission,
    session: AsyncSession = Depends(get_session),
) -> FeedbackRecordResponse:
    """Compatibility alias for the requested unversioned feedback path."""
    return await submit_feedback(payload, session)


@router.get("/admin/feedback", response_model=FeedbackListResponse)
async def list_feedback(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    feedback_type: FeedbackType | None = Query(None),
    feedback_reason: FeedbackReason | None = Query(None),
    knowledge_base_id: str | None = Query(None, max_length=64),
    generation_id: str | None = Query(None, max_length=64),
    answer_status: str | None = Query(None, max_length=32),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> FeedbackListResponse:
    records, total = await AnswerFeedbackService(session).list_feedback(
        offset=offset,
        limit=limit,
        feedback_type=feedback_type,
        feedback_reason=feedback_reason,
        knowledge_base_id=knowledge_base_id,
        generation_id=generation_id,
        answer_status=answer_status,
        created_from=created_from,
        created_to=created_to,
    )
    return FeedbackListResponse(
        items=[_record_response(record) for record in records],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/admin/feedback/metrics")
async def feedback_metrics(
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await AnswerFeedbackService(session).metrics()


@router.patch("/admin/feedback/{record_id}/review", response_model=FeedbackRecordResponse)
async def update_feedback_review(
    record_id: str,
    payload: FeedbackReviewSubmission,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> FeedbackRecordResponse:
    try:
        record = await AnswerFeedbackService(session).update_review(
            record_id=record_id,
            values=payload.model_dump(exclude_none=True),
        )
    except FeedbackNotFoundError as error:
        raise AppError(
            AppErrorCode.feedback_not_found,
            "评审样本不存在。",
            status_code=404,
        ) from error
    except FeedbackValidationError as error:
        raise AppError(str(error), str(error), status_code=422) from error
    return _record_response(record)


@router.get("/admin/feedback/export", response_model=None)
async def export_feedback(
    format: Literal["json", "csv"] = Query("json"),
    feedback_type: FeedbackType | None = Query(None),
    feedback_reason: FeedbackReason | None = Query(None),
    knowledge_base_id: str | None = Query(None, max_length=64),
    generation_id: str | None = Query(None, max_length=64),
    answer_status: str | None = Query(None, max_length=32),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> Response | dict[str, list[dict]]:
    records, _ = await AnswerFeedbackService(session).list_feedback(
        offset=0,
        limit=100,
        feedback_type=feedback_type,
        feedback_reason=feedback_reason,
        knowledge_base_id=knowledge_base_id,
        generation_id=generation_id,
        answer_status=answer_status,
        created_from=created_from,
        created_to=created_to,
    )
    rows = [_export_row(record) for record in records]
    if format == "json":
        return {"items": rows}

    output = io.StringIO()
    fieldnames = list(rows[0]) if rows else [
        "question",
        "answer",
        "knowledge_base_id",
        "retrieved_chunks",
        "citations",
        "feedback_reason",
        "review_result",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: value if isinstance(value, str) else _json_value(value)
            for key, value in row.items()
        })
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=phase11-feedback.csv"},
    )


def _export_row(record: AnswerFeedbackRecord) -> dict[str, object]:
    return {
        "question": record.question,
        "answer": record.answer,
        "knowledge_base_id": record.knowledge_base_id,
        "retrieved_chunks": record.retrieved_chunks or [],
        "citations": record.citations or [],
        "feedback_reason": record.feedback_reason,
        "review_result": {
            "answer_correct": record.answer_correct,
            "answer_complete": record.answer_complete,
            "citation_supported": record.citation_supported,
            "refusal_appropriate": record.refusal_appropriate,
            "root_cause": record.root_cause,
            "review_notes": record.review_notes,
        },
    }


def _json_value(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
