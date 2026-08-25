from __future__ import annotations

import json

import httpx
from app.api_client import ApiError, KnowledgeApiClient


def test_submit_feedback_sends_only_feedback_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    client = KnowledgeApiClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ),
        api_key="service-key",
    )

    client.submit_feedback(
        request_id="req-1",
        feedback_type="unhelpful",
        feedback_reason="answer_incorrect",
        feedback_comment="答案不对",
    )

    assert captured["path"] == "/v1/feedback"
    assert captured["json"] == {
        "request_id": "req-1",
        "feedback_type": "unhelpful",
        "feedback_reason": "answer_incorrect",
        "feedback_comment": "答案不对",
    }


def test_submit_feedback_maps_api_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "request_id": "req-missing",
                "trace_id": "trace-1",
                "code": "FEEDBACK_NOT_FOUND",
                "message": "该请求没有可反馈的业务回答。",
                "retryable": False,
            },
        )

    client = KnowledgeApiClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ),
        api_key="service-key",
    )

    try:
        client.submit_feedback(request_id="req-missing", feedback_type="helpful")
    except ApiError as error:
        assert error.code == "FEEDBACK_NOT_FOUND"
        assert error.status_code == 404
    else:
        raise AssertionError("expected ApiError")
