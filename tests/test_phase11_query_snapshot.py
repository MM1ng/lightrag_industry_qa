from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import BackgroundTasks
from industrial_rag.api import QueryResponse, _queue_answer_snapshot, _snapshot_answer_status
from industrial_rag.db.models import AnswerFeedbackRecord
from industrial_rag.db.session import close_db, get_session_factory, init_db, reset_for_testing
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
    database_path = tmp_path / "query-snapshot.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    reset_for_testing()
    _run(init_db(drop_all=True))
    yield
    reset_for_testing()
    os.environ.pop("DATABASE_URL", None)


def _response(status: str) -> QueryResponse:
    return QueryResponse(
        request_id="req-queue",
        trace_id="trace-queue",
        status=status,
        answer="回答或拒答",
        citations=[],
        claims=[],
        latency_ms=10,
    )


def test_only_business_answer_statuses_queue_snapshot_tasks() -> None:
    tasks = BackgroundTasks()
    for status in ("success", "insufficient_evidence", "safety_blocked"):
        _queue_answer_snapshot(
            tasks,
            request_id=f"req-{status}",
            trace_id="trace",
            generation_id="generation",
            knowledge_base_id="kb",
            question="问题",
            response=_response(status),
            retrieval_trace=None,
        )

    assert len(tasks.tasks) == 3
    assert _snapshot_answer_status("error") is None


def test_queued_snapshot_does_not_change_response_and_persists_business_status() -> None:
    async def _test():
        tasks = BackgroundTasks()
        response = _response("success")
        _queue_answer_snapshot(
            tasks,
            request_id="req-background",
            trace_id="trace-background",
            generation_id="generation-background",
            knowledge_base_id="kb-background",
            question="原问题",
            response=response,
            retrieval_trace=None,
        )
        await tasks()
        async with get_session_factory()() as session:
            record = (
                await session.execute(
                    select(AnswerFeedbackRecord).where(
                        AnswerFeedbackRecord.request_id == "req-background"
                    )
                )
            ).scalar_one()
            return response, record

    response, record = _run(_test())
    assert response.answer == "回答或拒答"
    assert response.status == "success"
    assert record.answer_status == "answered"
    assert record.question == "原问题"
