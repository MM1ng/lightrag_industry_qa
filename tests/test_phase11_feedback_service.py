from __future__ import annotations

import asyncio
import os

import pytest
from industrial_rag.db.models import AnswerFeedbackRecord
from industrial_rag.db.session import close_db, get_session_factory, init_db, reset_for_testing
from industrial_rag.services.answer_feedback_service import (
    AnswerFeedbackService,
    FeedbackValidationError,
    extract_retrieved_chunk_summaries,
)
from sqlalchemy import select


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
        loop.run_until_complete(close_db())
        return result
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _db(tmp_path):
    database_path = tmp_path / "feedback-service.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    reset_for_testing()
    _run(init_db(drop_all=True))
    yield
    reset_for_testing()
    os.environ.pop("DATABASE_URL", None)


def test_snapshot_persists_only_business_answer_statuses() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-answered",
            trace_id="trace-1",
            generation_id="gen-1",
            knowledge_base_id="kb-1",
            question="问题一",
            answer="答案一",
            answer_status="answered",
            citations=[{"chunk_id": "c1"}],
            retrieved_chunks=[{"chunk_id": "c1", "content_excerpt": "证据"}],
        )
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-error",
            trace_id="trace-2",
            generation_id="gen-1",
            knowledge_base_id="kb-1",
            question="问题二",
            answer="错误",
            answer_status="error",
            citations=[],
            retrieved_chunks=[],
        )
        async with get_session_factory()() as session:
            rows = (await session.execute(select(AnswerFeedbackRecord))).scalars().all()
            return rows

    rows = _run(_test())
    assert len(rows) == 1
    assert rows[0].request_id == "req-answered"


def test_persistence_filters_retrieved_chunk_fields_again() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-bounded",
            trace_id=None,
            generation_id=None,
            knowledge_base_id="kb-1",
            question="问题",
            answer="答案",
            answer_status="answered",
            citations=[],
            retrieved_chunks=[
                {
                    "chunk_id": "c1",
                    "document_name": "manual.pdf",
                    "page": 1,
                    "initial_rank": 1,
                    "reranked_rank": None,
                    "score": 0.5,
                    "content_excerpt": "x" * 1000,
                    "full_trace": "must not persist",
                }
            ],
        )
        async with get_session_factory()() as session:
            return (
                await session.execute(select(AnswerFeedbackRecord))
            ).scalar_one()

    record = _run(_test())
    assert set(record.retrieved_chunks[0]) == {
        "chunk_id",
        "document_name",
        "page",
        "initial_rank",
        "reranked_rank",
        "score",
        "content_excerpt",
    }
    assert len(record.retrieved_chunks[0]["content_excerpt"]) == 240


def test_retrieved_chunk_summary_is_bounded_and_does_not_store_full_trace() -> None:
    trace = {
        "initial_results": [
            {
                "chunk_id": f"c-{index}",
                "document_name": "manual.pdf",
                "page_number": index + 1,
                "initial_rank": index + 1,
                "initial_score": 0.9,
                "reranked_rank": None,
                "reranked_score": None,
                "content_excerpt": "x" * 1000,
            }
            for index in range(30)
        ],
        "reranked_results": [],
    }

    summaries = extract_retrieved_chunk_summaries(trace)

    assert len(summaries) == 20
    assert set(summaries[0]) == {
        "chunk_id",
        "document_name",
        "page",
        "initial_rank",
        "reranked_rank",
        "score",
        "content_excerpt",
    }
    assert len(summaries[0]["content_excerpt"]) <= 240


def test_duplicate_feedback_updates_one_snapshot() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-1",
            trace_id="trace-1",
            generation_id="gen-1",
            knowledge_base_id="kb-1",
            question="问题",
            answer="答案",
            answer_status="answered",
            citations=[],
            retrieved_chunks=[],
        )
        async with get_session_factory()() as session:
            service = AnswerFeedbackService(session)
            first = await service.submit_feedback(
                request_id="req-1",
                feedback_type="helpful",
                feedback_reason=None,
                feedback_comment=None,
            )
            second = await service.submit_feedback(
                request_id="req-1",
                feedback_type="unhelpful",
                feedback_reason="answer_incorrect",
                feedback_comment="需要修正",
            )
            await session.commit()
            return first, second

    first, second = _run(_test())
    assert first.id == second.id
    assert second.feedback_type == "unhelpful"
    assert second.feedback_reason == "answer_incorrect"


def test_unhelpful_feedback_requires_fixed_reason() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-1",
            trace_id=None,
            generation_id=None,
            knowledge_base_id="kb-1",
            question="问题",
            answer="答案",
            answer_status="insufficient_evidence",
            citations=[],
            retrieved_chunks=[],
        )
        async with get_session_factory()() as session:
            return await AnswerFeedbackService(session).submit_feedback(
                request_id="req-1",
                feedback_type="unhelpful",
                feedback_reason=None,
                feedback_comment=None,
            )

    with pytest.raises(FeedbackValidationError, match="feedback_reason"):
        _run(_test())
