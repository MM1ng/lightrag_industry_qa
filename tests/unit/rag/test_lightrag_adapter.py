from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from pytest_httpx import HTTPXMock

from industrial_energy_agent.rag.base import (
    CitationSource,
    RAGApplicationError,
    RAGCapabilityError,
    RAGConflictError,
    RAGDocument,
    RAGInvalidRequestError,
    RAGRateLimitError,
    RAGResponseError,
    RAGUnauthorizedError,
    RAGUnavailableError,
)
from industrial_energy_agent.rag.lightrag_adapter import LightRAGRestAdapter


class StubSourceResolver:
    def resolve_sources(self, source_ids: tuple[str, ...]) -> list[CitationSource]:
        return [
            CitationSource(reference_id=source_id, file_source=f"{source_id}.md")
            for source_id in source_ids
        ]


@pytest.fixture
def adapter() -> LightRAGRestAdapter:
    return LightRAGRestAdapter(
        base_url="http://127.0.0.1:9621",
        api_key=SecretStr("test-only-lightrag"),
        timeout_seconds=1,
        max_retries=0,
        source_resolver=StubSourceResolver(),
    )


def _query_response(*, status: str = "success") -> dict[str, object]:
    return {
        "status": status,
        "message": "ok" if status == "success" else "upstream detail must not leak",
        "data": {
            "chunks": [],
            "entities": [],
            "relationships": [],
            "references": [],
        },
        "metadata": {},
    }


def _document_payload(file_path: str, *, metadata: object = None) -> dict[str, object]:
    return {
        "id": f"doc-{file_path}",
        "content_summary": "summary",
        "content_length": 7,
        "status": "PROCESSED",
        "created_at": "2026-07-21T00:00:00+00:00",
        "updated_at": "2026-07-21T00:00:01+00:00",
        "track_id": "insert_test",
        "chunks_count": 1,
        "error_msg": None,
        "metadata": metadata,
        "file_path": file_path,
    }


@pytest.mark.parametrize(
    "file_source", ["../manual.txt", "folder/manual.txt", "folder\\manual.txt"]
)
def test_document_file_source_must_be_a_basename(file_source: str) -> None:
    with pytest.raises(ValidationError, match="basename"):
        RAGDocument(text="body", file_source=file_source)


@pytest.mark.parametrize("mode", ["local", "global", "hybrid", "naive", "mix"])
def test_search_maps_only_verified_modes(
    mode: str,
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/query/data",
        json=_query_response(),
    )

    result = adapter.search("轴承温度异常", mode=mode, top_k=5)

    assert result.mode == mode
    request = httpx_mock.get_request()
    assert request is not None
    assert request.read().decode("utf-8")
    assert request.headers["X-API-Key"] == "test-only-lightrag"
    assert "Authorization" not in request.headers


def test_search_rejects_unverified_bypass_mode(adapter: LightRAGRestAdapter) -> None:
    with pytest.raises(RAGCapabilityError):
        adapter.search("轴承温度异常", mode="bypass", top_k=5)  # type: ignore[arg-type]


def test_adapter_sends_dedicated_x_api_key(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:9621/health",
        json={
            "status": "healthy",
            "working_directory": "data/processed/lightrag/storage",
            "core_version": "1.5.4",
            "configuration": {
                "llm_binding": "openai",
                "llm_binding_host": (
                    "https://workspace-never-render.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
                ),
                "llm_model": "qwen3.7-plus",
                "embedding_binding": "openai",
                "embedding_model": "text-embedding-v4",
            },
        },
    )

    health = adapter.health_check()

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-API-Key"] == "test-only-lightrag"
    assert "Authorization" not in request.headers
    assert health.configuration.llm_model == "qwen3.7-plus"
    assert "workspace-never-render" not in repr(health)


def test_track_status_normalizes_nullable_metadata(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(
        json={
            "track_id": "insert_test",
            "documents": [_document_payload("manual-marker.txt", metadata=None)],
            "total_count": 1,
            "status_summary": {"PROCESSED": 1},
        }
    )

    result = adapter.track_status("insert_test")

    assert result.documents[0].metadata == {}


def test_http_200_failure_envelope_is_not_an_empty_success(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(json=_query_response(status="failure"))

    with pytest.raises(RAGApplicationError, match="RAG application request failed") as error:
        adapter.search("轴承温度异常", mode="hybrid", top_k=5)

    assert "upstream detail" not in str(error.value)


def test_query_data_requires_all_verified_nested_arrays(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    payload = _query_response()
    assert isinstance(payload["data"], dict)
    del payload["data"]["references"]
    httpx_mock.add_response(json=payload)

    with pytest.raises(RAGResponseError):
        adapter.search("轴承温度异常", mode="hybrid", top_k=5)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, RAGUnauthorizedError),
        (403, RAGUnauthorizedError),
        (409, RAGConflictError),
        (422, RAGInvalidRequestError),
        (429, RAGRateLimitError),
        (500, RAGUnavailableError),
    ],
)
def test_http_errors_are_normalized_without_upstream_detail(
    status_code: int,
    error_type: type[Exception],
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(status_code=status_code, json={"detail": "secret upstream detail"})

    with pytest.raises(error_type) as error:
        adapter.search("轴承温度异常", mode="hybrid", top_k=5)

    assert "secret upstream detail" not in str(error.value)


def test_transport_timeout_is_normalized(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("never expose request details"))

    with pytest.raises(RAGUnavailableError) as error:
        adapter.health_check()

    assert "never expose" not in str(error.value)


def test_ingest_uses_single_and_batch_routes(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/documents/text",
        json={"status": "success", "message": "accepted", "track_id": "insert_single"},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/documents/texts",
        json={"status": "success", "message": "accepted", "track_id": "insert_batch"},
    )

    single = adapter.ingest_documents(
        [RAGDocument(text="single body", file_source="single-marker.txt")]
    )
    batch = adapter.ingest_documents(
        [
            RAGDocument(text="batch one", file_source="batch-one-marker.txt"),
            RAGDocument(text="batch two", file_source="batch-two-marker.txt"),
        ]
    )

    assert single.track_id == "insert_single"
    assert batch.track_id == "insert_batch"
    requests = httpx_mock.get_requests()
    assert requests[0].url.path == "/documents/text"
    assert requests[1].url.path == "/documents/texts"


def test_insert_failure_is_not_automatically_replayed() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, request=request, json={"detail": "failed"})

    client = httpx.Client(
        base_url="http://127.0.0.1:9621",
        transport=httpx.MockTransport(handler),
    )
    adapter = LightRAGRestAdapter(
        base_url="http://127.0.0.1:9621",
        api_key=SecretStr("test-only-lightrag"),
        max_retries=2,
        retry_base_delay_seconds=0,
        client=client,
    )

    with pytest.raises(RAGUnavailableError):
        adapter.ingest_documents([RAGDocument(text="body", file_source="manual-marker.txt")])

    assert attempts == 1


def test_reconciliation_uses_paginated_documents_and_references(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:9621/documents/track_status/insert_test",
        json={
            "track_id": "insert_test",
            "documents": [],
            "total_count": 0,
            "status_summary": {},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/documents/paginated",
        json={
            "documents": [],
            "pagination": {
                "page": 1,
                "page_size": 50,
                "total_count": 0,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            },
            "status_counts": {},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/query/data",
        json=_query_response(),
    )

    result = adapter.reconcile_file_source("manual-marker.txt", track_id="insert_test")

    assert result.probes == frozenset({"track_status", "documents_paginated", "query_references"})
    assert result.confirmed is False


def test_reconciliation_scans_all_document_pages(
    httpx_mock: HTTPXMock,
    adapter: LightRAGRestAdapter,
) -> None:
    target = _document_payload("manual-marker.txt", metadata={})
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:9621/documents/track_status/insert_test",
        json={
            "track_id": "insert_test",
            "documents": [target],
            "total_count": 1,
            "status_summary": {"PROCESSED": 1},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/documents/paginated",
        json={
            "documents": [_document_payload("newer-file.txt", metadata={})],
            "pagination": {
                "page": 1,
                "page_size": 50,
                "total_count": 51,
                "total_pages": 2,
                "has_next": True,
                "has_prev": False,
            },
            "status_counts": {"PROCESSED": 51},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/documents/paginated",
        json={
            "documents": [target],
            "pagination": {
                "page": 2,
                "page_size": 50,
                "total_count": 51,
                "total_pages": 2,
                "has_next": False,
                "has_prev": True,
            },
            "status_counts": {"PROCESSED": 51},
        },
    )
    response = _query_response()
    assert isinstance(response["data"], dict)
    response["data"]["references"] = [{"reference_id": "1", "file_path": "manual-marker.txt"}]
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:9621/query/data",
        json=response,
    )

    result = adapter.reconcile_file_source("manual-marker.txt", track_id="insert_test")

    assert result.confirmed is True
    assert result.paginated_match is True


def test_get_sources_uses_local_manifest_without_remote_call(
    adapter: LightRAGRestAdapter,
) -> None:
    sources = adapter.get_sources(["ref-1", "ref-2"])

    assert [source.file_source for source in sources] == ["ref-1.md", "ref-2.md"]


def test_local_filters_are_not_silently_sent_to_lightrag(
    adapter: LightRAGRestAdapter,
) -> None:
    with pytest.raises(RAGCapabilityError):
        adapter.search(
            "轴承温度异常",
            mode="hybrid",
            top_k=5,
            local_filters={"equipment_type": "pump"},
        )


def test_business_package_never_imports_lightrag() -> None:
    package_root = Path(__file__).resolve().parents[3] / "src" / "industrial_energy_agent"
    imported_roots: set[str] = set()
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert "lightrag" not in imported_roots
