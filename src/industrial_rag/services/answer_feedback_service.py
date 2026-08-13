"""Small answer-quality feedback service with bounded persisted samples."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from industrial_rag.db.session import get_session_factory
from industrial_rag.repositories.answer_feedback_repository import (
    AnswerFeedbackRepository,
)

logger = logging.getLogger(__name__)

ELIGIBLE_ANSWER_STATUSES = frozenset(
    {"answered", "insufficient_evidence", "refused"}
)
FEEDBACK_TYPES = frozenset({"helpful", "unhelpful"})
NEGATIVE_FEEDBACK_REASONS = frozenset(
    {
        "answer_incorrect",
        "citation_unsupported",
        "answer_incomplete",
        "answer_not_found",
        "false_refusal",
        "unsafe_or_unnecessary_answer",
        "response_too_slow",
        "other",
    }
)
REVIEW_VALUES = frozenset({"true", "false", "unknown", "not_applicable"})
ROOT_CAUSES = frozenset(
    {
        "retrieval_failure",
        "rerank_failure",
        "answer_generation_failure",
        "citation_failure",
        "refusal_failure",
        "knowledge_gap",
        "question_unclear",
        "unknown",
    }
)
MAX_RETRIEVED_CHUNKS = 20
MAX_CONTENT_EXCERPT = 240
MAX_FEEDBACK_COMMENT = 1000


class FeedbackValidationError(ValueError):
    """Raised when feedback fields violate the Phase 11 contract."""


class FeedbackNotFoundError(LookupError):
    """Raised when feedback targets no persisted business answer."""


class AnswerFeedbackService:
    def __init__(self, session) -> None:
        self._repository = AnswerFeedbackRepository(session)

    @staticmethod
    async def record_answer_best_effort(
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
    ) -> None:
        if answer_status not in ELIGIBLE_ANSWER_STATUSES:
            return
        try:
            factory = get_session_factory()
            async with factory() as session, session.begin():
                await AnswerFeedbackRepository(session).create_or_get_by_request_id(
                    request_id=request_id,
                    trace_id=trace_id,
                    generation_id=generation_id,
                    knowledge_base_id=knowledge_base_id,
                    question=question,
                    answer=answer,
                    answer_status=answer_status,
                    citations=_bounded_json_list(citations),
                    retrieved_chunks=_sanitize_retrieved_chunks(retrieved_chunks),
                )
        except Exception as error:  # best effort by contract
            logger.warning(
                "Answer feedback snapshot write failed request_id=%s error_type=%s",
                request_id,
                type(error).__name__,
            )

    async def submit_feedback(
        self,
        *,
        request_id: str,
        feedback_type: str,
        feedback_reason: str | None,
        feedback_comment: str | None,
    ):
        reason = _validate_feedback(
            feedback_type=feedback_type,
            feedback_reason=feedback_reason,
            feedback_comment=feedback_comment,
        )
        record = await self._repository.update_feedback_by_request_id(
            request_id=request_id,
            feedback_type=feedback_type,
            feedback_reason=reason,
            feedback_comment=_normalize_comment(feedback_comment),
        )
        if record is None:
            raise FeedbackNotFoundError(request_id)
        return record

    async def list_feedback(self, **filters):
        return await self._repository.list_filtered(**filters)

    async def update_review(self, *, record_id: str, values: dict[str, str | None]):
        for key, value in values.items():
            if key in {
                "answer_correct",
                "answer_complete",
                "citation_supported",
                "refusal_appropriate",
            } and value is not None and value not in REVIEW_VALUES:
                raise FeedbackValidationError(f"{key} 评审值无效")
            if key == "root_cause" and value is not None and value not in ROOT_CAUSES:
                raise FeedbackValidationError("root_cause 评审值无效")
            if key == "review_notes" and value is not None and len(value) > 2000:
                raise FeedbackValidationError("review_notes 超过长度限制")
        record = await self._repository.update_review_by_id(
            record_id=record_id,
            values=values,
        )
        if record is None:
            raise FeedbackNotFoundError(record_id)
        return record

    async def metrics(self) -> dict[str, object]:
        records = await self._repository.list_all()
        total = len(records)
        feedback_count = sum(record.feedback_type in FEEDBACK_TYPES for record in records)
        negative_count = sum(record.feedback_type == "unhelpful" for record in records)
        answered = [record for record in records if record.answer_status == "answered"]
        citation_count = sum(_has_valid_citation(record.citations) for record in answered)
        empty_evidence_count = sum(not record.retrieved_chunks for record in answered)
        refused_count = sum(record.answer_status == "refused" for record in records)
        return {
            "feedback_coverage_count": feedback_count,
            "feedback_coverage_rate": _rate(feedback_count, total),
            "negative_feedback_count": negative_count,
            "negative_feedback_rate_among_feedback": _rate(negative_count, feedback_count),
            "negative_feedback_rate_among_eligible_answers": _rate(negative_count, total),
            "citation_presence_rate": _rate(citation_count, len(answered)),
            "empty_evidence_answer_rate": _rate(empty_evidence_count, len(answered)),
            "refusal_rate": _rate(refused_count, total),
        }


def extract_retrieved_chunk_summaries(
    trace: Mapping[str, Any] | Any,
) -> list[dict[str, Any]]:
    """Convert an in-memory trace into bounded evaluation-only summaries."""

    initial = _trace_value(trace, "initial_results", ())
    reranked = _trace_value(trace, "reranked_results", ())
    by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for source, items in (("initial", initial), ("reranked", reranked)):
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            continue
        for item in items:
            chunk_id = _string_value(item, "chunk_id")
            document_name = _string_value(item, "document_name")
            page = _positive_int_value(item, "page_number") or _positive_int_value(item, "page")
            if not chunk_id or not document_name or page is None:
                continue
            key = (chunk_id, document_name, page)
            summary = by_key.setdefault(
                key,
                {
                    "chunk_id": chunk_id,
                    "document_name": document_name,
                    "page": page,
                    "initial_rank": None,
                    "reranked_rank": None,
                    "score": None,
                    "content_excerpt": "",
                },
            )
            if source == "initial":
                summary["initial_rank"] = _positive_int_value(item, "initial_rank")
                summary["score"] = _score_value(item, "initial_score")
            else:
                summary["reranked_rank"] = _positive_int_value(item, "reranked_rank")
                summary["score"] = (
                    _score_value(item, "reranked_score")
                    or summary["score"]
                )
            excerpt = _string_value(item, "content_excerpt")
            if excerpt:
                summary["content_excerpt"] = excerpt[:MAX_CONTENT_EXCERPT]
    return list(by_key.values())[:MAX_RETRIEVED_CHUNKS]


def _validate_feedback(
    *, feedback_type: str, feedback_reason: str | None, feedback_comment: str | None
) -> str | None:
    if feedback_type not in FEEDBACK_TYPES:
        raise FeedbackValidationError("feedback_type 无效")
    if feedback_type == "unhelpful" and feedback_reason not in NEGATIVE_FEEDBACK_REASONS:
        raise FeedbackValidationError("unhelpful feedback_reason 必须为固定原因")
    if feedback_type == "helpful" and feedback_reason is not None:
        raise FeedbackValidationError("helpful 不需要 feedback_reason")
    if feedback_comment is not None and len(feedback_comment.strip()) > MAX_FEEDBACK_COMMENT:
        raise FeedbackValidationError("feedback_comment 超过长度限制")
    return feedback_reason


def _normalize_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    normalized = comment.strip()
    return normalized or None


def _bounded_json_list(value: list[dict]) -> list[dict]:
    return [item for item in value[:MAX_RETRIEVED_CHUNKS] if isinstance(item, dict)]


def _sanitize_retrieved_chunks(value: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for item in value[:MAX_RETRIEVED_CHUNKS]:
        if not isinstance(item, Mapping):
            continue
        chunk_id = item.get("chunk_id")
        document_name = item.get("document_name")
        page = item.get("page")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            continue
        if not isinstance(document_name, str) or not document_name.strip():
            continue
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            continue
        sanitized.append(
            {
                "chunk_id": chunk_id.strip(),
                "document_name": document_name.strip(),
                "page": page,
                "initial_rank": _optional_positive_int(item.get("initial_rank")),
                "reranked_rank": _optional_positive_int(item.get("reranked_rank")),
                "score": _optional_score(item.get("score")),
                "content_excerpt": str(item.get("content_excerpt") or "")[:MAX_CONTENT_EXCERPT],
            }
        )
    return sanitized


def _optional_positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _optional_score(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _has_valid_citation(citations: object) -> bool:
    if not isinstance(citations, list):
        return False
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("chunk_id"), str)
        and bool(item["chunk_id"].strip())
        for item in citations
    )


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _trace_value(trace: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    if isinstance(trace, Mapping):
        return trace.get(key, default)
    return getattr(trace, key, default)


def _string_value(item: Any, key: str) -> str:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return value.strip() if isinstance(value, str) else ""


def _positive_int_value(item: Any, key: str) -> int | None:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _score_value(item: Any, key: str) -> float | None:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
