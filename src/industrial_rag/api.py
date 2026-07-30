"""Minimal FastAPI adapter for the synchronous LightRAG runtime."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, QueryResult
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
    status: Literal["success", "insufficient_evidence"]
    answer: str
    citations: list[CitationResponse]
    claims: list[ClaimResponse]
    latency_ms: int


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
}


def _request_id() -> str:
    return uuid4().hex


def _request_id_for(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = _request_id()
        request.state.request_id = request_id
    return request_id


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


def create_app(
    *,
    settings: Settings | None = None,
    runtime_factory: Callable[[Settings], QueryRuntime] = LightRAGRuntime,
) -> FastAPI:
    """Create an API whose settings and runtime are resolved during lifespan startup."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime: QueryRuntime | None = None
        resolved_settings: Settings | None = None
        application.state.runtime = None
        application.state.service_api_key = None
        try:
            resolved_settings = settings or Settings.from_env()
            application.state.service_api_key = resolved_settings.service_api_key
            runtime = runtime_factory(resolved_settings)
            application.state.runtime = runtime
        except Exception:
            if resolved_settings is None and settings is None:
                application.state.service_api_key = _service_api_key_from_environment()
        try:
            yield
        finally:
            if runtime is not None:
                runtime.close()
            application.state.runtime = None

    application = FastAPI(lifespan=lifespan)

    @application.middleware("http")
    async def authenticate_query_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST" or request.url.path != "/v1/query":
            return await call_next(request)
        request_id = _request_id_for(request)
        service_api_key: str | None = request.app.state.service_api_key
        if service_api_key is None:
            return await call_next(request)
        expected = f"Bearer {service_api_key}".encode()
        supplied = (request.headers.get("Authorization") or "").encode()
        if secrets.compare_digest(supplied, expected):
            return await call_next(request)
        _log_result(request_id=request_id, status="UNAUTHORIZED", latency_ms=0)
        return _error_response("UNAUTHORIZED", request_id=request_id)

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

    @application.get("/readyz", response_model=None)
    def readyz(request: Request) -> dict[str, str] | JSONResponse:
        if request.app.state.runtime is None:
            return _error_response("INDEX_NOT_READY")
        return {"status": "ready"}

    @application.post("/v1/query", response_model=QueryResponse)
    def query(
        payload: QueryRequest,
        request: Request,
    ) -> QueryResponse | JSONResponse:
        request_id = _request_id_for(request)
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
        return response

    return application


app = create_app()
