from __future__ import annotations

import logging
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from industrial_rag.api import QueryResponse, create_app
from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Document,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import get_session_factory, init_db, reset_for_testing
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.operational_metrics import operational_metrics
from industrial_rag.repositories.retrieval_trace_repository import (
    RetrievalTraceRepository,
)
from industrial_rag.retrieval_trace import (
    TRACE_VERSION,
    RetrievalExecutionTrace,
    RetrievalTraceItem,
    SelectedEvidenceTrace,
)
from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager

SERVICE_KEY = "phase10a-service-test-credential"
ADMIN_KEY = "phase10a-admin-test-credential"
KB_ID = "a" * 32
GENERATION_ID = "b" * 32


def _trace(question: str) -> RetrievalExecutionTrace:
    item = RetrievalTraceItem(
        initial_rank=1,
        initial_score=None,
        retrieval_source="lightrag_mix_unspecified",
        document_id=None,
        document_name="manual.pdf",
        page_number=7,
        chunk_id="chunk-7",
        matched_terms=("轴承",),
        used_for_answer=True,
        cited_in_answer=True,
    )
    selected = SelectedEvidenceTrace(
        final_rank=1,
        chunk_id="chunk-7",
        document_id=None,
        document_name="manual.pdf",
        page_number=7,
        initial_rank=1,
        reranked_rank=None,
        used_for_answer=True,
        cited_in_answer=True,
    )
    return RetrievalExecutionTrace(
        trace_version=TRACE_VERSION,
        original_query=question,
        normalized_query=question.strip(),
        retrieval_config=(
            ("mode", "mix"),
            ("top_k", 12),
            ("chunk_top_k", 20),
            ("rerank_enabled", False),
        ),
        initial_results=(item,),
        rerank_applied=False,
        reranked_results=(),
        final_selected_chunks=(selected,),
        selected_chunk_ids=("chunk-7",),
        normalization_ms=0.1,
        retrieval_ms=4.2,
        rerank_ms=0.0,
        evidence_selection_ms=0.3,
    )


class DiagnosticRuntime:
    query_calls = 0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.initialized = False

    async def query(self, question: str, *, mode: str) -> QueryResult:
        type(self).query_calls += 1
        return QueryResult(
            answer="应检查轴承润滑。",
            citations=(Citation("manual.pdf", 7, "chunk-7"),),
            mode="mix",
            retrieval_chunk_ids=("chunk-7",),
            retrieval_meta=(("manual.pdf", 7, "chunk-7"),),  # type: ignore[arg-type]
            retrieval_trace=_trace(question),
        )


def _auth(token: str | None) -> dict[str, str]:
    return {} if token is None else {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def diagnostic_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "diagnostics.db"
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    reset_for_testing()
    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        session.add_all(
            [
                KnowledgeBase(
                    id=KB_ID,
                    name="diagnostics",
                    status="ready",
                    workspace_path=str(tmp_path / "workspace"),
                    upload_path=str(tmp_path / "uploads"),
                    parsed_path=str(tmp_path / "parsed"),
                    vector_backend="qdrant",
                    active_vector_generation_id=GENERATION_ID,
                    generation_epoch=3,
                ),
                VectorIndexGeneration(
                    id=GENERATION_ID,
                    knowledge_base_id=KB_ID,
                    backend="qdrant",
                    generation="g-phase10a",
                    status=VectorIndexGenerationStatus.active,
                    workspace_path=str(tmp_path / "workspace"),
                    collections={"chunks": "phase10a_chunks"},
                    document_manifest_hash="1" * 64,
                    child_chunks_manifest_hash="2" * 64,
                    embedding_config_hash="3" * 64,
                    chunking_config_hash="4" * 64,
                ),
                Document(
                    id="c" * 32,
                    knowledge_base_id=KB_ID,
                    original_file_name="manual.pdf",
                    logical_name="manual.pdf",
                    stored_file_name="manual.pdf",
                    file_path=str(tmp_path / "manual.pdf"),
                    file_hash="5" * 64,
                    status="indexed",
                    is_active=True,
                    parse_status="done",
                    index_status="done",
                ),
            ]
        )
        await session.commit()

    settings = Settings(
        api_key="offline-provider-key",
        service_api_key=SERVICE_KEY,
        admin_api_key=ADMIN_KEY,
        working_dir=tmp_path,
        vector_backend="qdrant",
        qdrant_url="http://127.0.0.1:1",
    )
    manager = KnowledgeBaseRuntimeManager(service_factory=DiagnosticRuntime)
    app = create_app(settings=settings)
    app.state.service_api_key = SERVICE_KEY
    app.state.admin_api_key = ADMIN_KEY
    app.state.resolved_settings = settings
    app.state.runtime_manager = manager
    DiagnosticRuntime.query_calls = 0
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await manager.close_all()
    reset_for_testing()


@pytest.mark.asyncio
async def test_diagnostic_route_auth_matrix_and_no_second_retrieval(
    diagnostic_client: AsyncClient,
) -> None:
    """Catches missing admin authorization or a diagnostic-only retrieval chain."""
    query_response = await diagnostic_client.post(
        f"/v1/knowledge-bases/{KB_ID}/query",
        json={"query": "轴承温度过高怎么办？"},
        headers=_auth(SERVICE_KEY),
    )
    assert query_response.status_code == 200
    assert set(query_response.json()) == set(QueryResponse.model_fields) - {"shadow_audit"}
    assert "initial_results" not in query_response.text
    request_id = query_response.json()["request_id"]
    url = f"/v1/admin/diagnostics/requests/{request_id}/retrieval-trace"

    assert (await diagnostic_client.get(url)).status_code == 401
    assert (
        await diagnostic_client.get(url, headers=_auth("wrong-token"))
    ).status_code == 401
    denied = await diagnostic_client.get(url, headers=_auth(SERVICE_KEY))
    assert (denied.status_code, denied.json()["code"]) == (
        403,
        "ADMIN_PERMISSION_REQUIRED",
    )
    allowed = await diagnostic_client.get(url, headers=_auth(ADMIN_KEY))
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["request_id"] == request_id
    assert payload["trace_version"] == TRACE_VERSION
    assert payload["knowledge_base_id"] == KB_ID
    assert payload["generation_id"] == GENERATION_ID
    assert payload["initial_results"][0]["initial_score"] is None
    assert payload["rerank_applied"] is False
    assert payload["reranked_results"] == []
    assert payload["final_selected_chunks"][0]["document_id"] == "c" * 32

    second_get = await diagnostic_client.get(url, headers=_auth(ADMIN_KEY))
    assert second_get.status_code == 200
    missing = await diagnostic_client.get(
        "/v1/admin/diagnostics/requests/missing/retrieval-trace",
        headers=_auth(ADMIN_KEY),
    )
    assert (missing.status_code, missing.json()["code"]) == (
        404,
        "RETRIEVAL_TRACE_NOT_FOUND",
    )
    assert DiagnosticRuntime.query_calls == 1


@pytest.mark.asyncio
async def test_rewrite_failure_logs_bounded_diagnostic_with_request_and_trace_ids(
    diagnostic_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="industrial_rag.api")

    response = await diagnostic_client.post(
        f"/v1/knowledge-bases/{KB_ID}/query",
        json={
            "query": "它多久维护一次？",
            "history": [
                {"role": "user", "content": "A 泵和 B 泵有什么区别？"},
                {"role": "assistant", "content": "历史技术事实不应进入诊断。"},
            ],
        },
        headers={**_auth(SERVICE_KEY), "x-trace-id": "trace-from-test"},
    )

    assert response.status_code == 422
    body = response.json()
    records = [
        record
        for record in caplog.records
        if record.message == "Query rewrite diagnostic"
    ]
    assert len(records) == 1
    diagnostic = records[0].query_rewrite_diagnostic
    assert diagnostic["request_id"] == body["request_id"]
    assert diagnostic["trace_id"] == "trace-from-test"
    assert diagnostic["rewrite_status"] == "ambiguous"
    assert diagnostic["failure_reason"] == "ambiguous_context"
    assert "历史技术事实" not in str(diagnostic)
    assert "history" not in diagnostic
    assert DiagnosticRuntime.query_calls == 0


@pytest.mark.asyncio
async def test_trace_write_failure_preserves_ordinary_response_and_is_sanitized(
    diagnostic_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Catches trace failure escaping into the query transaction or leaking payloads."""
    before = operational_metrics.snapshot()["counters"].get(
        "retrieval_trace_write_failure_total", 0
    )

    async def fail_insert(*args, **kwargs):
        raise RuntimeError("do-not-log-query-or-credentials")

    monkeypatch.setattr(RetrievalTraceRepository, "create_immutable", fail_insert)
    caplog.set_level(logging.WARNING)
    response = await diagnostic_client.post(
        f"/v1/knowledge-bases/{KB_ID}/query",
        json={"query": "敏感查询正文"},
        headers=_auth(SERVICE_KEY),
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "应检查轴承润滑。"
    after = operational_metrics.snapshot()["counters"].get(
        "retrieval_trace_write_failure_total", 0
    )
    assert after == before + 1
    combined = response.text + caplog.text
    for forbidden in (SERVICE_KEY, ADMIN_KEY, "敏感查询正文", "do-not-log"):
        assert forbidden not in combined
