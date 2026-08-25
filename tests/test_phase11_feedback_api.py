from __future__ import annotations

import asyncio
import csv
import io
import os

import pytest
from httpx import ASGITransport, AsyncClient
from industrial_rag.api import create_app
from industrial_rag.db.session import close_db, get_session_factory, init_db, reset_for_testing
from industrial_rag.services.answer_feedback_service import AnswerFeedbackService

SERVICE_KEY = "phase11-service-key"
ADMIN_KEY = "phase11-admin-key"


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
    database_path = tmp_path / "feedback-api.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    reset_for_testing()
    _run(init_db(drop_all=True))
    yield
    reset_for_testing()
    os.environ.pop("DATABASE_URL", None)


def _make_app():
    app = create_app()
    app.state.service_api_key = SERVICE_KEY
    app.state.admin_api_key = ADMIN_KEY
    app.state.runtime = None
    return app


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _seed_snapshot(
    *,
    request_id: str,
    feedback_type: str | None = None,
    feedback_reason: str | None = None,
    answer_status: str = "answered",
    citations: list[dict] | None = None,
    retrieved_chunks: list[dict] | None = None,
) -> None:
    async def _seed():
        await AnswerFeedbackService.record_answer_best_effort(
            request_id=request_id,
            trace_id=f"trace-{request_id}",
            generation_id="generation-real",
            knowledge_base_id="kb-real",
            question=f"问题-{request_id}",
            answer=f"答案-{request_id}",
            answer_status=answer_status,
            citations=citations or [],
            retrieved_chunks=(
                retrieved_chunks
                if retrieved_chunks is not None
                else [
                    {
                        "chunk_id": "chunk-1",
                        "document_name": "manual.pdf",
                        "page": 2,
                        "initial_rank": 1,
                        "reranked_rank": None,
                        "score": 0.8,
                        "content_excerpt": "证据摘要",
                    }
                ]
            ),
        )
        if feedback_type is not None:
            async with get_session_factory()() as session:
                await AnswerFeedbackService(session).submit_feedback(
                    request_id=request_id,
                    feedback_type=feedback_type,
                    feedback_reason=feedback_reason,
                    feedback_comment=None,
                )
                await session.commit()

    _run(_seed())


def test_helpful_feedback_submit_success() -> None:
    _seed_snapshot(request_id="req-helpful")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={"request_id": "req-helpful", "feedback_type": "helpful"},
            )

    response = _run(_test())
    assert response.status_code == 200
    assert response.json()["feedback_type"] == "helpful"
    assert response.json()["question"] == "问题-req-helpful"


def test_unversioned_feedback_alias_is_available() -> None:
    _seed_snapshot(request_id="req-api-alias")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/feedback",
                headers=_headers(SERVICE_KEY),
                json={"request_id": "req-api-alias", "feedback_type": "helpful"},
            )

    response = _run(_test())
    assert response.status_code == 200


def test_unhelpful_feedback_requires_reason() -> None:
    _seed_snapshot(request_id="req-unhelpful")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={"request_id": "req-unhelpful", "feedback_type": "unhelpful"},
            )

    response = _run(_test())
    assert response.status_code == 422


def test_unknown_request_id_returns_not_found() -> None:
    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={
                    "request_id": "missing",
                    "feedback_type": "helpful",
                },
            )

    response = _run(_test())
    assert response.status_code == 404


def test_client_cannot_forge_answer_or_lineage_fields() -> None:
    _seed_snapshot(request_id="req-trusted")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={
                    "request_id": "req-trusted",
                    "feedback_type": "unhelpful",
                    "feedback_reason": "other",
                    "feedback_comment": "说明",
                    "answer": "伪造答案",
                    "generation_id": "伪造Generation",
                    "knowledge_base_id": "伪造KB",
                    "citations": [{"chunk_id": "伪造引用"}],
                },
            )

    response = _run(_test())
    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "答案-req-trusted"
    assert body["generation_id"] == "generation-real"
    assert body["knowledge_base_id"] == "kb-real"
    assert body["citations"] == []


def test_duplicate_feedback_updates_existing_row() -> None:
    _seed_snapshot(request_id="req-duplicate")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={"request_id": "req-duplicate", "feedback_type": "helpful"},
            )
            second = await client.post(
                "/v1/feedback",
                headers=_headers(SERVICE_KEY),
                json={
                    "request_id": "req-duplicate",
                    "feedback_type": "unhelpful",
                    "feedback_reason": "answer_incorrect",
                },
            )
            return first, second

    first, second = _run(_test())
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["feedback_type"] == "unhelpful"


def test_admin_can_filter_negative_feedback_and_return_metrics() -> None:
    _seed_snapshot(
        request_id="req-negative",
        feedback_type="unhelpful",
        feedback_reason="citation_unsupported",
        citations=[{"chunk_id": "c1"}],
    )
    _seed_snapshot(
        request_id="req-positive",
        feedback_type="helpful",
        citations=[],
        retrieved_chunks=[],
    )
    _seed_snapshot(request_id="req-refused", answer_status="refused")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            rows = await client.get(
                "/v1/admin/feedback",
                headers=_headers(ADMIN_KEY),
                params={"feedback_type": "unhelpful", "feedback_reason": "citation_unsupported"},
            )
            metrics = await client.get(
                "/v1/admin/feedback/metrics",
                headers=_headers(ADMIN_KEY),
            )
            return rows, metrics

    rows, metrics = _run(_test())
    assert rows.status_code == 200
    assert rows.json()["total"] == 1
    assert rows.json()["items"][0]["request_id"] == "req-negative"
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["feedback_coverage_rate"] == {"numerator": 2, "denominator": 3, "value": 2 / 3}
    assert body["negative_feedback_rate_among_feedback"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert body["negative_feedback_rate_among_eligible_answers"] == {"numerator": 1, "denominator": 3, "value": 1 / 3}
    assert body["citation_presence_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert body["empty_evidence_answer_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert body["refusal_rate"] == {"numerator": 1, "denominator": 3, "value": 1 / 3}


def test_admin_can_export_bounded_json_and_csv() -> None:
    _seed_snapshot(request_id="req-export", feedback_type="unhelpful", feedback_reason="other")

    async def _test():
        async with AsyncClient(
            transport=ASGITransport(app=_make_app()), base_url="http://test"
        ) as client:
            json_response = await client.get(
                "/v1/admin/feedback/export",
                headers=_headers(ADMIN_KEY),
            )
            csv_response = await client.get(
                "/v1/admin/feedback/export",
                headers=_headers(ADMIN_KEY),
                params={"format": "csv"},
            )
            return json_response, csv_response

    json_response, csv_response = _run(_test())
    assert json_response.status_code == 200
    row = json_response.json()["items"][0]
    assert {"question", "answer", "knowledge_base_id", "retrieved_chunks", "citations", "feedback_reason", "review_result"} <= set(row)
    assert csv_response.status_code == 200
    parsed = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert parsed[0]["question"] == "问题-req-export"
