"""FastAPI application with KB lifecycle + legacy query compatibility."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.db.session import close_db, get_session, init_db
from industrial_rag.errors import AppError
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, QueryResult
from industrial_rag.routers import documents, knowledge_bases, tasks
from industrial_rag.runtime import LightRAGRuntime

logger = logging.getLogger(__name__)

QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
HistoryContent = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class QueryRuntime(Protocol):
    """Runtime operations used by the HTTP adapter."""

    def query(
        self,
        question: str,
        *,
        mode: Literal["mix"],
        timeout: float,
    ) -> tuple[QueryResult, float]: ...

    def close(self) -> None: ...


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: HistoryContent


class QueryRequest(BaseModel):
    query: QueryText
    history: list[HistoryMessage] = Field(default_factory=list, max_length=10)


class CitationResponse(BaseModel):
    citation_id: str
    document_name: str
    page: int
    chunk_id: str


class ClaimResponse(BaseModel):
    claim_id: str
    text: str
    citation_ids: list[str]


class QueryResponse(BaseModel):
    request_id: str
    trace_id: str = ""
    status: Literal["success", "insufficient_evidence"]
    answer: str
    citations: list[CitationResponse]
    claims: list[ClaimResponse]
    latency_ms: int
    retrieved_chunk_ids: list[str] = []
    shadow_audit: dict[str, Any] | None = None


class PublicError(BaseModel):
    request_id: str
    code: str
    message: str
    retryable: bool


_ERRORS: dict[str, tuple[int, str, bool]] = {
    "INVALID_REQUEST": (422, "请求内容不合法，请检查后重试。", False),
    "UNAUTHORIZED": (401, "未提供有效的服务凭据。", False),
    "INDEX_NOT_READY": (503, "知识库索引尚未就绪，请稍后重试。", True),
    "TIMEOUT": (504, "知识库查询超时，请稍后重试。", True),
    "UPSTREAM_UNAVAILABLE": (502, "知识库服务暂时不可用，请稍后重试。", True),
    "EMPTY_QUESTION": (422, "问题不能为空。", False),
    "KB_NOT_FOUND": (404, "知识库不存在。", False),
    "GENERATION_NOT_READY": (503, "知识库生成尚未就绪，请稍后重试。", True),
    "RETRIEVAL_FAILED": (502, "检索服务暂时不可用，请稍后重试。", True),
    "EMBEDDING_FAILED": (502, "向量服务暂时不可用，请稍后重试。", True),
    "ANSWER_MODEL_FAILED": (502, "答案模型暂时不可用，请稍后重试。", True),
    "QA_TIMEOUT": (504, "问答请求超时，请稍后重试。", True),
    "SAFETY_POLICY_BLOCKED": (403, "该请求涉及高风险操作或超出系统安全边界，系统仅提供信息检索与分析，请人工复核。", False),
    "CITATION_AUDIT_WARNING": (200, "引用审计发现警告，请人工复核。", False),
    "INTERNAL_ERROR": (500, "系统内部错误，请稍后重试。", False),
}


def _request_id() -> str:
    return uuid4().hex


def _trace_id() -> str:
    return uuid4().hex


def _request_id_for(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = _request_id()
        request.state.request_id = request_id
    return request_id


def _trace_id_for(request: Request) -> str:
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id is None:
        trace_id = request.headers.get("x-trace-id") or _trace_id()
        request.state.trace_id = trace_id
    return trace_id


def _error_response(
    code: str,
    *,
    request_id: str | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    default_status_code, message, retryable = _ERRORS[code]
    body = PublicError(
        request_id=request_id or _request_id(),
        code=code,
        message=message,
        retryable=retryable,
    )
    return JSONResponse(
        status_code=status_code if status_code is not None else default_status_code,
        content=body.model_dump(),
        headers=headers,
    )


def _citation_response(citation: Citation, index: int) -> CitationResponse:
    return CitationResponse(
        citation_id=f"cite_{index}",
        document_name=citation.source_file,
        page=citation.page_number,
        chunk_id=citation.chunk_id,
    )


def _shadow_audit_record(
    *,
    request_id: str,
    kb_id: str | None,
    generation: str | None,
    result: QueryResult,
) -> dict[str, Any]:
    """Non-blocking citation audit record (never alters the answer)."""
    from industrial_rag.shadow_audit import CitationShadowAudit

    audit = CitationShadowAudit(
        request_id=request_id,
        question_id=None,
        kb_id=kb_id,
        generation=generation,
        citations=tuple(
            {
                "chunk_id": citation.chunk_id,
                "document_name": citation.source_file,
                "page": citation.page_number,
            }
            for citation in result.citations
        ),
        context_chunk_ids=tuple(result.retrieval_chunk_ids),
        retrieved_chunk_ids=tuple(result.retrieval_chunk_ids),
        context_registry=tuple(result.retrieval_meta),
    )
    return audit.record


def _log_result(*, request_id: str, status: str, latency_ms: int) -> None:
    logger.info(
        "API request completed",
        extra={
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
        },
    )


def _service_api_key_from_environment() -> str | None:
    return (os.environ.get("SERVICE_API_KEY") or "").strip() or None


# ---------------------------------------------------------------------------
# Query schema with optional knowledge_base_id
# ---------------------------------------------------------------------------


class QueryRequestV2(BaseModel):
    query: QueryText
    history: list[HistoryMessage] = Field(default_factory=list, max_length=10)
    knowledge_base_id: str | None = Field(default=None, max_length=64)


def _query_schema(history: list[dict[str, str]]) -> dict[str, object]:
    """Backward-compatible v1 query schema: no knowledge_base_id."""
    return {
        "query": QueryText,
        "history": list[HistoryMessage],  # type: ignore[dict-item]
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    settings: Settings | None = None,
    runtime_factory: Callable[[Settings], QueryRuntime] = LightRAGRuntime,
) -> FastAPI:
    """Create an API whose settings and runtime are resolved during lifespan startup."""

    # Shared state visible to all routes
    _runtime_manager: Any = None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal _runtime_manager
        runtime: QueryRuntime | None = None
        resolved_settings: Settings | None = None
        application.state.runtime = None
        application.state.resolved_settings = None
        application.state.service_api_key = None
        application.state.runtime_manager = None

        # Init DB
        await init_db()

        try:
            resolved_settings = settings or Settings.from_env()
            application.state.resolved_settings = resolved_settings
            application.state.service_api_key = resolved_settings.service_api_key
            runtime = runtime_factory(resolved_settings)
            application.state.runtime = runtime

            # Create runtime manager for multi-KB support
            from industrial_rag.services.runtime_manager import (
                KnowledgeBaseRuntimeManager,
            )
            _runtime_manager = KnowledgeBaseRuntimeManager()
            application.state.runtime_manager = _runtime_manager

            # Start lifecycle task executor
            # Import handler impls so they self-register
            import industrial_rag.services.handler_impls  # noqa: F401
            from industrial_rag.db.session import get_session_factory
            from industrial_rag.services.lifecycle_task_executor import (
                LifecycleTaskExecutor,
            )

            _executor = LifecycleTaskExecutor(
                get_session_factory(),
                settings=resolved_settings,
                runtime_manager=_runtime_manager,
            )
            await _executor.start()
            application.state.task_executor = _executor
        except Exception:
            if resolved_settings is None and settings is None:
                application.state.service_api_key = _service_api_key_from_environment()
        try:
            yield
        finally:
            executor = getattr(application.state, "task_executor", None)
            if executor is not None:
                await executor.stop()
            if runtime is not None:
                runtime.close()
            if _runtime_manager is not None:
                await _runtime_manager.close_all()
            await close_db()
            application.state.runtime = None
            application.state.resolved_settings = None

    application = FastAPI(lifespan=lifespan)

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    @application.middleware("http")
    async def authenticate_query_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Authenticate mutation endpoints (POST/PATCH/DELETE to v1/knowledge-bases, v1/tasks)
        # and legacy /v1/query
        if request.method in ("GET", "OPTIONS", "HEAD"):
            return await call_next(request)
        request_id = _request_id_for(request)
        _trace_id_for(request)
        service_api_key: str | None = request.app.state.service_api_key
        if service_api_key is None:
            return await call_next(request)
        path = request.url.path
        if path == "/readyz" or path == "/healthz":
            return await call_next(request)
        expected = f"Bearer {service_api_key}".encode()
        supplied = (request.headers.get("Authorization") or "").encode()
        if secrets.compare_digest(supplied, expected):
            return await call_next(request)
        _log_result(request_id=request_id, status="UNAUTHORIZED", latency_ms=0)
        return _error_response("UNAUTHORIZED", request_id=request_id)

    # ------------------------------------------------------------------
    # Exception handlers
    # ------------------------------------------------------------------

    @application.exception_handler(StarletteHTTPException)
    async def framework_http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        return _error_response(
            "INVALID_REQUEST",
            request_id=_request_id_for(request),
            status_code=error.status_code,
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response("INVALID_REQUEST", request_id=_request_id_for(request))

    @application.exception_handler(AppError)
    async def app_error_handler(
        request: Request,
        error: AppError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": _request_id_for(request),
                    "details": error.details,
                }
            },
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @application.get("/readyz", response_model=None)
    def readyz(request: Request) -> dict[str, str] | JSONResponse:
        if request.app.state.runtime is None:
            return _error_response("INDEX_NOT_READY")
        return {"status": "ready"}

    @application.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        try:
            async for _ in get_session():
                break  # DB available
        except Exception:
            return {"status": "degraded", "db": "unavailable"}
        return {"status": "ok", "db": "available"}

    @application.get("/health")
    async def health(request: Request) -> dict[str, str]:
        """Liveness: the application process is alive."""
        return {"status": "ok", "service": "industrial-rag-qa"}

    @application.get("/version")
    async def version(request: Request) -> dict[str, object]:
        """Version surface (no secrets)."""
        from industrial_rag.production_config import ProductionQASettings
        from industrial_rag.version import version_info

        info = version_info()
        try:
            qa = ProductionQASettings.from_env()
        except Exception:
            qa = None
        return {
            "app_version": info["app_version"],
            "release_channel": info["release_channel"],
            "git_commit": info["git_commit"],
            "config_version": info["config_version"],
            "strategy_version": info["strategy_version"],
            "build_time": info["build_time"],
            "parser_pipeline": qa.parser_pipeline if qa else None,
            "query_mode": qa.query_mode if qa else None,
            "answer_model": qa.answer_model if qa else None,
            "embedding_model": qa.embedding_model if qa else None,
        }

    @application.get("/ready", response_model=None)
    def ready(request: Request) -> dict[str, object] | JSONResponse:
        """Readiness: config legal, DB reachable, Qdrant reachable (when used)."""
        components: dict[str, str] = {"config": "unknown", "db": "unknown", "qdrant": "n/a"}
        resolved = getattr(request.app.state, "resolved_settings", None)
        if resolved is None:
            components["config"] = "not_loaded"
            components["db"] = "unknown"
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "components": components,
                    "message": "runtime not initialized",
                },
            )
        components["config"] = "ok"
        try:
            import asyncio

            async def _db_ok() -> None:
                async for _ in get_session():
                    break

            asyncio.run(_db_ok())
            components["db"] = "ok"
        except Exception:
            components["db"] = "unavailable"
        if resolved.vector_backend.value == "qdrant":
            try:
                import asyncio

                from qdrant_client import AsyncQdrantClient

                async def _qdrant_ok() -> bool:
                    client = AsyncQdrantClient(url=resolved.qdrant_url, timeout=5)
                    try:
                        await client.get_collections()
                        return True
                    finally:
                        await client.close()

                components["qdrant"] = "ok" if asyncio.run(_qdrant_ok()) else "unavailable"
            except Exception:
                components["qdrant"] = "unavailable"
        ready_status = components.get("db") == "ok" and components.get("qdrant") in {"ok", "n/a"}
        return {
            "status": "ready" if ready_status else "not_ready",
            "components": components,
        }

    # ------------------------------------------------------------------
    # Legacy query (backward compatible)
    # ------------------------------------------------------------------

    @application.post("/v1/query", response_model=QueryResponse)
    def query(
        payload: QueryRequest,
        request: Request,
    ) -> QueryResponse | JSONResponse:
        request_id = _request_id_for(request)
        trace_id = _trace_id_for(request)
        from industrial_rag.safety_policy import evaluate_input

        safety = evaluate_input(payload.query)
        if not safety.allowed:
            response = _error_response(
                "SAFETY_POLICY_BLOCKED",
                request_id=request_id,
                status_code=403,
            )
            _log_result(request_id=request_id, status="SAFETY_POLICY_BLOCKED", latency_ms=0)
            return response
        runtime: QueryRuntime | None = request.app.state.runtime
        if runtime is None:
            response = _error_response("INDEX_NOT_READY", request_id=request_id)
            _log_result(request_id=request_id, status="INDEX_NOT_READY", latency_ms=0)
            return response

        try:
            result, latency_seconds = runtime.query(
                payload.query,
                mode="mix",
                timeout=180.0,
            )
        except Exception as error:
            code = (
                "TIMEOUT"
                if isinstance(error, TimeoutError) or "timed out" in str(error).casefold()
                else "UPSTREAM_UNAVAILABLE"
            )
            response = _error_response(code, request_id=request_id)
            _log_result(request_id=request_id, status=code, latency_ms=0)
            return response

        latency_ms = round(latency_seconds * 1000)
        if result.answer == INSUFFICIENT_EVIDENCE_MESSAGE or not result.citations:
            response = QueryResponse(
                request_id=request_id,
                trace_id=trace_id,
                status="insufficient_evidence",
                answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                citations=[],
                claims=[],
                latency_ms=latency_ms,
            )
            _log_result(
                request_id=request_id,
                status="insufficient_evidence",
                latency_ms=latency_ms,
            )
            return response

        citations = [
            _citation_response(citation, index)
            for index, citation in enumerate(result.citations, start=1)
        ]
        response = QueryResponse(
            request_id=request_id,
            trace_id=trace_id,
            status="success",
            answer=result.answer,
            citations=citations,
            claims=[
                ClaimResponse(
                    claim_id="claim_1",
                    text=result.answer,
                    citation_ids=[citation.citation_id for citation in citations],
                )
            ],
            latency_ms=latency_ms,
        )
        _log_result(request_id=request_id, status="success", latency_ms=latency_ms)
        if os.environ.get("CITATION_SHADOW_AUDIT_ENABLED", "false").lower() == "true":
            from industrial_rag.shadow_audit import CitationShadowAudit

            audit = CitationShadowAudit(
                request_id=request_id,
                question_id=None,
                kb_id=None,
                generation=None,
                citations=tuple(
                    {
                        "chunk_id": citation.chunk_id,
                        "document_name": citation.source_file,
                        "page": citation.page_number,
                    }
                    for citation in result.citations
                ),
                context_chunk_ids=tuple(result.retrieval_chunk_ids),
                retrieved_chunk_ids=tuple(result.retrieval_chunk_ids),
                context_registry=tuple(result.retrieval_meta),
            )
            if request.headers.get("x-debug-audit") == "1":
                response.shadow_audit = audit.record
        return response

    # ------------------------------------------------------------------
    # KB-scoped query
    # ------------------------------------------------------------------

    @application.post("/v1/knowledge-bases/{kb_id}/query", response_model=QueryResponse)
    async def query_kb(
        kb_id: str,
        payload: QueryRequest,
        request: Request,
    ) -> QueryResponse | JSONResponse:
        request_id = _request_id_for(request)
        trace_id = _trace_id_for(request)
        from industrial_rag.safety_policy import evaluate_input

        safety = evaluate_input(payload.query)
        if not safety.allowed:
            return _error_response(
                "SAFETY_POLICY_BLOCKED",
                request_id=request_id,
                status_code=403,
            )
        runtime_manager = getattr(request.app.state, "runtime_manager", None)
        base_settings = getattr(request.app.state, "resolved_settings", None)
        if runtime_manager is None or base_settings is None:
            return _error_response("INDEX_NOT_READY", request_id=request_id)

        from industrial_rag.db.session import get_session_factory
        from industrial_rag.errors import AppErrorCode
        from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
        from industrial_rag.repositories.knowledge_base_repository import (
            KnowledgeBaseRepository,
        )

        async with get_session_factory()() as session:
            kb = await KnowledgeBaseRepository(session).get(kb_id)
            if kb is None or kb.status.value in {"deleting", "deleted"}:
                raise AppError(AppErrorCode.knowledge_base_not_found, "知识库不存在")
            kb_settings = settings_for_knowledge_base(base_settings, kb)
        try:
            result = await (await runtime_manager.get_runtime(kb_id, kb_settings)).query(
                payload.query,
                mode="mix",
            )
        except TimeoutError:
            _log_result(request_id=request_id, status="TIMEOUT", latency_ms=0)
            return _error_response("TIMEOUT", request_id=request_id)
        except AppError:
            raise
        except Exception:
            _log_result(request_id=request_id, status="UPSTREAM_UNAVAILABLE", latency_ms=0)
            return _error_response("UPSTREAM_UNAVAILABLE", request_id=request_id)

        if result.answer == INSUFFICIENT_EVIDENCE_MESSAGE or not result.citations:
            return QueryResponse(
                request_id=request_id,
                trace_id=trace_id,
                status="insufficient_evidence",
                answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                citations=[],
                claims=[],
                latency_ms=0,
                retrieved_chunk_ids=list(result.retrieval_chunk_ids),
                shadow_audit=(
                    _shadow_audit_record(
                        request_id=request_id,
                        kb_id=kb_id,
                        generation=getattr(kb_settings, "qdrant_generation", None),
                        result=result,
                    )
                    if os.environ.get("CITATION_SHADOW_AUDIT_ENABLED", "false").lower() == "true"
                    and request.headers.get("x-debug-audit") == "1"
                    else None
                ),
            )
        citations = [
            _citation_response(citation, index)
            for index, citation in enumerate(result.citations, start=1)
        ]
        return QueryResponse(
            request_id=request_id,
            trace_id=trace_id,
            status="success",
            answer=result.answer,
            citations=citations,
            claims=[
                ClaimResponse(
                    claim_id="claim_1",
                    text=result.answer,
                    citation_ids=[citation.citation_id for citation in citations],
                )
            ],
            latency_ms=0,
            retrieved_chunk_ids=list(result.retrieval_chunk_ids),
            shadow_audit=(
                _shadow_audit_record(
                    request_id=request_id,
                    kb_id=kb_id,
                    generation=getattr(kb_settings, "qdrant_generation", None),
                    result=result,
                )
                if os.environ.get("CITATION_SHADOW_AUDIT_ENABLED", "false").lower() == "true"
                and request.headers.get("x-debug-audit") == "1"
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Register new phase-2 routers
    # ------------------------------------------------------------------

    application.include_router(knowledge_bases.router)
    application.include_router(documents.router)
    application.include_router(tasks.router)

    return application


app = create_app()
