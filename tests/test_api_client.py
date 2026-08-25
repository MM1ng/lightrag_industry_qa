"""Offline behavior tests for the Streamlit P3 HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest
from app.api_client import ApiCitation, ApiError, KnowledgeApiClient


def _client(handler: httpx.MockTransport) -> KnowledgeApiClient:
    return KnowledgeApiClient(
        "http://knowledge-api.test",
        api_key="test-key",
        http_client=httpx.Client(
            base_url="http://knowledge-api.test",
            transport=handler,
        ),
    )


def test_query_maps_p3_safe_citation_and_sends_recent_history() -> None:
    """Catch a client that uses the old LightRAG citation schema or omits history."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/query"
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "query": "离心泵启动前需要检查什么？",
            "history": [{"role": "user", "content": "上一轮问题"}],
        }
        return httpx.Response(
            200,
            json={
                "request_id": "req_abc",
                "status": "success",
                "answer": "检查轴承润滑状态。",
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "text": "检查轴承润滑状态。",
                        "citation_ids": ["cite_1"],
                    }
                ],
                "citations": [
                    {
                        "citation_id": "cite_1",
                        "document_id": "doc_1",
                        "document_name": "pump-manual.pdf",
                        "page": 7,
                        "chunk_id": "pump-p7-c1",
                        "excerpt": "检查轴承润滑状态。",
                    }
                ],
                "latency_ms": 1234,
            },
            request=request,
        )

    result = _client(httpx.MockTransport(handler)).query(
        "离心泵启动前需要检查什么？",
        history=[{"role": "user", "content": "上一轮问题"}],
    )

    assert result.request_id == "req_abc"
    assert result.status == "success"
    assert result.answer == "检查轴承润滑状态。"
    assert result.citations[0].source_file == "pump-manual.pdf"
    assert result.citations[0].page_number == 7
    assert result.claims[0].citation_ids == ("cite_1",)
    assert result.latency_ms == 1234


def test_query_exposes_only_public_p3_error() -> None:
    """Catch a client that hides a usable public error or leaks a raw response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"request_id": "req_busy", "code": "SERVICE_BUSY", "message": "服务繁忙，请稍后重试。"},
            request=request,
        )

    with pytest.raises(ApiError) as raised:
        _client(httpx.MockTransport(handler)).query("离心泵启动前需要检查什么？")

    assert raised.value.code == "SERVICE_BUSY"
    assert raised.value.message == "服务繁忙，请稍后重试。"
    assert raised.value.status_code == 503


def test_query_replaces_unrecognized_error_body_with_safe_message() -> None:
    """Catch an upstream 5xx body being shown as if it were a P3 public error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"code": "INTERNAL", "message": "traceback includes secret details"},
            request=request,
        )

    with pytest.raises(ApiError) as raised:
        _client(httpx.MockTransport(handler)).query("离心泵启动前需要检查什么？")

    assert raised.value.code == "UPSTREAM_UNAVAILABLE"
    assert raised.value.message == "知识库服务暂时不可用，请稍后重试。"


def test_query_replaces_malformed_upstream_error_with_safe_message() -> None:
    """Catch a client that surfaces a non-public server response body to the chat UI."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"internal stack trace: secret", request=request)

    with pytest.raises(ApiError) as raised:
        _client(httpx.MockTransport(handler)).query("离心泵启动前需要检查什么？")

    assert raised.value.code == "UPSTREAM_UNAVAILABLE"
    assert raised.value.message == "知识库服务暂时不可用，请稍后重试。"


def test_query_uses_legacy_citation_fields_when_preferred_fields_are_empty() -> None:
    """Catch citations disappearing when a mixed-version API leaves P3 keys empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "req_legacy_citation",
                "status": "success",
                "answer": "检查轴承润滑状态。",
                "claims": [],
                "citations": [
                    {
                        "document_name": None,
                        "source_file": "legacy-pump.pdf",
                        "page": None,
                        "page_number": 4,
                        "chunk_id": "legacy-chunk",
                    }
                ],
                "latency_ms": 10,
            },
            request=request,
        )

    result = _client(httpx.MockTransport(handler)).query("离心泵启动前需要检查什么？")

    assert result.citations == (ApiCitation("legacy-pump.pdf", 4, "legacy-chunk"),)


def test_ready_returns_false_when_service_is_unreachable() -> None:
    """Catch readiness checks that raise transport errors instead of keeping the UI usable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    assert _client(httpx.MockTransport(handler)).ready() is False


def test_ready_uses_a_short_timeout_instead_of_the_query_timeout() -> None:
    """Catch a readiness probe that can block the Streamlit page for a full query timeout."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 5.0
        return httpx.Response(200, json={"status": "ready"}, request=request)

    client = KnowledgeApiClient(
        "http://knowledge-api.test",
        http_client=httpx.Client(
            base_url="http://knowledge-api.test",
            timeout=120.0,
            transport=httpx.MockTransport(handler),
        ),
    )

    assert client.ready() is True
