"""Offline contract tests for the minimal FastAPI question-answering service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, QueryResult


class FakeRuntime:
    """Synchronous stand-in that records only the public runtime call boundary."""

    def __init__(
        self,
        *,
        result: QueryResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or _success_result()
        self.error = error
        self.calls: list[tuple[str, Literal["mix"]]] = []
        self.close_calls = 0

    def query(
        self,
        question: str,
        *,
        mode: Literal["mix"],
        timeout: float,
    ) -> tuple[QueryResult, float]:
        self.calls.append((question, mode))
        assert timeout == 180.0
        if self.error is not None:
            raise self.error
        return self.result, 0.123

    def close(self) -> None:
        self.close_calls += 1


def _success_result() -> QueryResult:
    return QueryResult(
        answer="请检查 E102 对应的传感器和接线。",
        citations=(Citation("设备维护手册.pdf", 12, "pump-p12-c3"),),
        mode="mix",
    )


def _app(
    runtime: FakeRuntime | None = None,
    *,
    service_api_key: str | None = None,
    runtime_factory: Callable[[Settings], FakeRuntime] | None = None,
):
    settings = Settings(api_key="offline-test-key", service_api_key=service_api_key)
    factory = runtime_factory or (lambda _: runtime if runtime is not None else FakeRuntime())
    return create_app(settings=settings, runtime_factory=factory)


def _headers(value: str | None) -> dict[str, str]:
    return {} if value is None else {"Authorization": value}


def _assert_public_error(response, *, status_code: int, code: str) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert body["code"] == code
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["retryable"], bool)


def test_readyz_and_lifespan_close_runtime() -> None:
    runtime = FakeRuntime()
    with TestClient(_app(runtime)) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
    assert runtime.close_calls == 1


def test_query_returns_traceable_citations_and_fixed_mix_mode() -> None:
    runtime = FakeRuntime(result=_success_result())
    with TestClient(_app(runtime)) as client:
        response = client.post(
            "/v1/query",
            json={"query": "E102 如何处理？", "history": [{"role": "user", "content": "x"}]},
        )
    body = response.json()
    assert response.status_code == 200
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert body["status"] == "success"
    assert body["answer"] == "请检查 E102 对应的传感器和接线。"
    assert body["latency_ms"] == 123
    assert body["citations"] == [
        {
            "citation_id": "cite_1",
            "document_name": "设备维护手册.pdf",
            "page": 12,
            "chunk_id": "pump-p12-c3",
        }
    ]
    assert body["claims"] == [
        {
            "claim_id": "claim_1",
            "text": body["answer"],
            "citation_ids": ["cite_1"],
        }
    ]
    assert runtime.calls == [("E102 如何处理？", "mix")]


def test_query_returns_insufficient_evidence_without_claims_or_citations() -> None:
    runtime = FakeRuntime(
        result=QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), "mix"),
    )
    with TestClient(_app(runtime)) as client:
        response = client.post("/v1/query", json={"query": "没有答案的问题"})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "insufficient_evidence"
    assert body["answer"] == INSUFFICIENT_EVIDENCE_MESSAGE
    assert body["citations"] == []
    assert body["claims"] == []


def test_query_accepts_exact_request_boundaries() -> None:
    runtime = FakeRuntime()
    history = [{"role": "user", "content": "h" * 2000} for _ in range(10)]
    query = "q" * 4000
    with TestClient(_app(runtime)) as client:
        response = client.post("/v1/query", json={"query": query, "history": history})
    assert response.status_code == 200
    assert runtime.calls == [(query, "mix")]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "x" * 4001},
        {"query": "问题", "history": [{"role": "user", "content": "x"}] * 11},
        {"query": "问题", "history": [{"role": "user", "content": "x" * 2001}]},
        {"query": "问题", "history": [{"role": "system", "content": "不允许"}]},
        {"query": "问题", "history": [{"role": "user", "content": ""}]},
    ],
)
def test_invalid_query_or_history_maps_to_invalid_request(payload: dict[str, object]) -> None:
    with TestClient(_app(FakeRuntime())) as client:
        response = client.post("/v1/query", json=payload)
    _assert_public_error(response, status_code=422, code="INVALID_REQUEST")


def test_query_allows_requests_when_service_api_key_is_not_configured() -> None:
    with TestClient(_app(FakeRuntime())) as client:
        response = client.post("/v1/query", json={"query": "问题"})
    assert response.status_code == 200


def test_history_is_neither_forwarded_nor_reused_between_requests() -> None:
    runtime = FakeRuntime()
    with TestClient(_app(runtime)) as client:
        first = client.post(
            "/v1/query",
            json={"query": "第一问", "history": [{"role": "user", "content": "仅限第一轮"}]},
        )
        second = client.post("/v1/query", json={"query": "第二问"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert runtime.calls == [("第一问", "mix"), ("第二问", "mix")]


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic expected-key"])
def test_query_rejects_missing_or_invalid_bearer_key(header: str | None) -> None:
    with TestClient(_app(FakeRuntime(), service_api_key="expected-key")) as client:
        response = client.post("/v1/query", json={"query": "问题"}, headers=_headers(header))
    _assert_public_error(response, status_code=401, code="UNAUTHORIZED")


@pytest.mark.parametrize("header", [None, "Bearer wrong"])
def test_query_authenticates_before_validating_malformed_payload(header: str | None) -> None:
    with TestClient(_app(FakeRuntime(), service_api_key="expected-key")) as client:
        response = client.post("/v1/query", json={"query": ""}, headers=_headers(header))
    _assert_public_error(response, status_code=401, code="UNAUTHORIZED")


@pytest.mark.parametrize("header", [None, "Bearer wrong"])
def test_query_authenticates_before_parsing_malformed_json(header: str | None) -> None:
    request_headers = {"Content-Type": "application/json", **_headers(header)}
    with TestClient(_app(FakeRuntime(), service_api_key="expected-key")) as client:
        response = client.post("/v1/query", content="{", headers=request_headers)
    _assert_public_error(response, status_code=401, code="UNAUTHORIZED")
    assert set(response.json()) == {"request_id", "code", "message", "retryable"}
    assert "json_invalid" not in response.text


def test_query_allows_correct_bearer_key() -> None:
    with TestClient(_app(FakeRuntime(), service_api_key="expected-key")) as client:
        response = client.post(
            "/v1/query",
            json={"query": "问题"},
            headers={"Authorization": "Bearer expected-key"},
        )
    assert response.status_code == 200


def test_query_timeout_maps_to_public_timeout_error() -> None:
    raw_error = "Query timed out after 1.0s"
    with TestClient(_app(FakeRuntime(error=RuntimeError(raw_error)))) as client:
        response = client.post("/v1/query", json={"query": "问题"})
    _assert_public_error(response, status_code=504, code="TIMEOUT")
    assert raw_error not in response.text


def test_other_runtime_error_hides_raw_exception_text() -> None:
    raw_error = "backend at https://secret.example.invalid failed"
    with TestClient(_app(FakeRuntime(error=RuntimeError(raw_error)))) as client:
        response = client.post("/v1/query", json={"query": "问题"})
    _assert_public_error(response, status_code=502, code="UPSTREAM_UNAVAILABLE")
    assert raw_error not in response.text


def test_runtime_factory_failure_yields_safe_not_ready_response() -> None:
    raw_error = "failed to open C:/private/index with credential secret"

    def fail_factory(_: Settings) -> FakeRuntime:
        raise RuntimeError(raw_error)

    with TestClient(_app(runtime_factory=fail_factory)) as client:
        response = client.get("/readyz")
    _assert_public_error(response, status_code=503, code="INDEX_NOT_READY")
    assert raw_error not in response.text
