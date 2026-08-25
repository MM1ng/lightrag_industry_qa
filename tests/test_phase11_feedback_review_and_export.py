from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from industrial_rag.api import create_app
from industrial_rag.db.session import close_db, init_db, reset_for_testing
from industrial_rag.services.answer_feedback_service import AnswerFeedbackService

ADMIN_KEY = "phase11-review-admin"


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
    database_path = tmp_path / "feedback-review.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    reset_for_testing()
    _run(init_db(drop_all=True))
    yield
    reset_for_testing()
    os.environ.pop("DATABASE_URL", None)


def test_admin_review_fields_accept_fixed_values_and_round_trip() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-review",
            trace_id="trace-review",
            generation_id="gen-review",
            knowledge_base_id="kb-review",
            question="问题",
            answer="答案",
            answer_status="answered",
            citations=[],
            retrieved_chunks=[],
        )
        app = create_app()
        app.state.service_api_key = "phase11-service"
        app.state.admin_api_key = ADMIN_KEY
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            rows = await client.get(
                "/v1/admin/feedback", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
            record_id = rows.json()["items"][0]["id"]
            update = await client.patch(
                f"/v1/admin/feedback/{record_id}/review",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"},
                json={
                    "answer_correct": "false",
                    "answer_complete": "unknown",
                    "citation_supported": "not_applicable",
                    "refusal_appropriate": "unknown",
                    "root_cause": "answer_generation_failure",
                    "review_notes": "答案需要补充条件",
                },
            )
            refreshed = await client.get(
                "/v1/admin/feedback", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
            return update, refreshed

    update, refreshed = _run(_test())
    assert update.status_code == 200
    assert update.json()["review_result"]["answer_correct"] == "false"
    assert refreshed.json()["items"][0]["review_result"]["root_cause"] == "answer_generation_failure"


def test_admin_review_rejects_unknown_values() -> None:
    async def _test():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id="req-invalid-review",
            trace_id=None,
            generation_id=None,
            knowledge_base_id="kb-review",
            question="问题",
            answer="答案",
            answer_status="refused",
            citations=[],
            retrieved_chunks=[],
        )
        app = create_app()
        app.state.service_api_key = "phase11-service"
        app.state.admin_api_key = ADMIN_KEY
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            rows = await client.get(
                "/v1/admin/feedback", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
            record_id = rows.json()["items"][0]["id"]
            return await client.patch(
                f"/v1/admin/feedback/{record_id}/review",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"},
                json={"answer_correct": "maybe"},
            )

    response = _run(_test())
    assert response.status_code == 422
