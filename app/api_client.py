"""Typed HTTP client for the P3 Knowledge QA API.

This module is deliberately independent from Streamlit and LightRAG.  It
normalizes the public P3 response contract into immutable UI-friendly values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

ApiStatus = Literal[
    "success",
    "partial_answer",
    "insufficient_evidence",
    "safety_blocked",
    "clarification_required",
    "out_of_scope",
    "failed",
]
_PUBLIC_ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "INDEX_NOT_READY",
        "UPSTREAM_UNAVAILABLE",
        "SERVICE_BUSY",
        "TIMEOUT",
        "INGESTION_IN_PROGRESS",
    }
)


@dataclass(frozen=True, slots=True)
class ApiCitation:
    """P3 citation fields used by the existing Streamlit chat UI."""

    source_file: str
    page_number: int
    chunk_id: str
    citation_id: str = ""
    evidence_id: str | None = None
    document_id: str | None = None
    generation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApiClaim:
    """One P3 answer claim and the citation IDs that support it."""

    claim_id: str
    text: str
    citation_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApiEvidence:
    evidence_id: str
    citation_id: str | None
    document_name: str
    document_id: str | None
    page: int
    chunk_id: str
    generation_id: str | None
    section_path: tuple[str, ...] = ()
    excerpt: str = ""
    source_type: str = "initial"
    context_role: str = "primary"
    supports_claim_ids: tuple[str, ...] = ()
    completion_reason: str | None = None
    relevance_label: str = "核心依据"


@dataclass(frozen=True, slots=True)
class ApiQueryResult:
    """Public P3 final-answer contract consumed by the Streamlit app."""

    request_id: str
    status: ApiStatus
    answer: str
    citations: tuple[ApiCitation, ...] = ()
    claims: tuple[ApiClaim, ...] = ()
    latency_ms: int = 0
    knowledge_base_id: str | None = None
    generation_id: str | None = None
    trace_id: str = ""
    evidence: tuple[ApiEvidence, ...] = ()
    partial_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKnowledgeBase:
    id: str
    name: str
    status: str
    document_count: int = 0
    active_document_count: int = 0
    chunk_count: int = 0
    active_generation: str | None = None
    updated_at: str | None = None


class ApiError(RuntimeError):
    """A safe, user-displayable API failure."""

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")


class KnowledgeApiClient:
    """Synchronous client for the P3 knowledge-query and readiness endpoints."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: str = "",
        timeout: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._headers = {"Content-Type": "application/json"}
        if api_key.strip():
            self._headers["Authorization"] = f"Bearer {api_key.strip()}"
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    def close(self) -> None:
        """Close the owned HTTP connection pool when the process exits."""
        if self._owns_client:
            self._client.close()

    def query(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> ApiQueryResult:
        """Submit one P3 question and parse its final response."""
        payload: dict[str, Any] = {"query": question, "history": history or []}
        try:
            response = self._client.post("/v1/query", json=payload, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise ApiError("TIMEOUT", "知识库服务响应超时，请稍后重试。", 504) from exc
        except httpx.HTTPError as exc:
            raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务暂时不可用，请稍后重试。", 502) from exc

        if response.status_code != 200:
            self._raise_public_error(response)
        return self._parse_response(response)

    def list_knowledge_bases(self) -> tuple[ApiKnowledgeBase, ...]:
        response = self._request("GET", "/v1/knowledge-bases")
        body = self._json_object(response)
        items = body.get("items", [])
        if not isinstance(items, list):
            return ()
        return tuple(self._parse_knowledge_base(item) for item in items if isinstance(item, dict))

    def get_knowledge_base(self, kb_id: str) -> ApiKnowledgeBase:
        if not kb_id.strip():
            raise ApiError("INVALID_REQUEST", "知识库 ID 不能为空。", 422)
        response = self._request("GET", f"/v1/knowledge-bases/{kb_id.strip()}")
        return self._parse_knowledge_base(self._json_object(response))

    def query_knowledge_base(
        self,
        kb_id: str,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> ApiQueryResult:
        if not kb_id.strip():
            raise ApiError("INVALID_REQUEST", "请先选择知识库。", 422)
        payload: dict[str, Any] = {"query": question, "history": history or []}
        response = self._request("POST", f"/v1/knowledge-bases/{kb_id.strip()}/query", json=payload)
        result = self._parse_response(response)
        return ApiQueryResult(
            request_id=result.request_id,
            status=result.status,
            answer=result.answer,
            citations=result.citations,
            claims=result.claims,
            latency_ms=result.latency_ms,
            knowledge_base_id=kb_id.strip(),
            generation_id=result.generation_id,
            trace_id=result.trace_id,
            evidence=result.evidence,
            partial_reason=result.partial_reason,
        )

    def ready(self) -> bool:
        """Return whether the API reports that it is ready for P3 queries."""
        try:
            return self._client.get("/readyz", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def _parse_response(self, response: httpx.Response) -> ApiQueryResult:
        body = self._json_object(response)

        answer = body.get("answer")
        status = body.get("status")
        if not isinstance(answer, str) or not answer.strip() or status not in {
            "success",
            "partial_answer",
            "insufficient_evidence",
            "safety_blocked",
            "clarification_required",
            "out_of_scope",
            "failed",
        }:
            raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务返回无效响应，请稍后重试。", 502)

        return ApiQueryResult(
            request_id=str(body.get("request_id", "")),
            status=status,
            answer=answer.strip(),
            citations=self._parse_citations(body.get("citations")),
            claims=self._parse_claims(body.get("claims")),
            latency_ms=self._nonnegative_int(body.get("latency_ms")),
            generation_id=body.get("generation_id") if isinstance(body.get("generation_id"), str) else None,
            trace_id=body.get("trace_id") if isinstance(body.get("trace_id"), str) else "",
            evidence=self._parse_evidence(body.get("evidence")),
            partial_reason=body.get("partial_reason") if isinstance(body.get("partial_reason"), str) else None,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, headers=self._headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiError("TIMEOUT", "知识库服务响应超时，请稍后重试。", 504) from exc
        except httpx.HTTPError as exc:
            raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务暂时不可用，请稍后重试。", 502) from exc
        if response.status_code != 200:
            self._raise_public_error(response)
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务返回无效响应，请稍后重试。", 502) from exc
        if not isinstance(body, dict):
            raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务返回无效响应，请稍后重试。", 502)
        return body

    @staticmethod
    def _parse_knowledge_base(item: dict[str, Any]) -> ApiKnowledgeBase:
        return ApiKnowledgeBase(
            id=str(item.get("id", "")),
            name=str(item.get("name", "未命名知识库")),
            status=str(item.get("status", "unknown")),
            document_count=KnowledgeApiClient._nonnegative_int(item.get("document_count")),
            active_document_count=KnowledgeApiClient._nonnegative_int(item.get("active_document_count")),
            chunk_count=KnowledgeApiClient._nonnegative_int(item.get("chunk_count")),
            active_generation=item.get("active_vector_generation") if isinstance(item.get("active_vector_generation"), str) else None,
            updated_at=item.get("updated_at") if isinstance(item.get("updated_at"), str) else None,
        )

    @staticmethod
    def _parse_citations(raw_citations: object) -> tuple[ApiCitation, ...]:
        if not isinstance(raw_citations, list):
            return ()
        citations: list[ApiCitation] = []
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            source_file = _first_nonempty_string(
                item.get("document_name"),
                item.get("source_file"),
            )
            page_number = _first_positive_int(item.get("page"), item.get("page_number"))
            chunk_id = item.get("chunk_id", "")
            if (
                source_file is None
                or page_number is None
                or not isinstance(chunk_id, str)
                or not chunk_id.strip()
            ):
                continue
            citations.append(
                ApiCitation(
                    source_file=source_file.strip(),
                    page_number=page_number,
                    chunk_id=chunk_id.strip(),
                )
            )
        return tuple(citations)

    @staticmethod
    def _parse_claims(raw_claims: object) -> tuple[ApiClaim, ...]:
        if not isinstance(raw_claims, list):
            return ()
        claims: list[ApiClaim] = []
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            claim_id = item.get("claim_id", "")
            text = item.get("text", "")
            citation_ids = item.get("citation_ids", [])
            if not isinstance(claim_id, str) or not isinstance(text, str) or not text.strip():
                continue
            safe_ids = tuple(value for value in citation_ids if isinstance(value, str)) if isinstance(citation_ids, list) else ()
            evidence_ids = item.get("evidence_ids", [])
            safe_evidence_ids = tuple(value for value in evidence_ids if isinstance(value, str)) if isinstance(evidence_ids, list) else ()
            claims.append(ApiClaim(claim_id=claim_id, text=text.strip(), citation_ids=safe_ids, evidence_ids=safe_evidence_ids))
        return tuple(claims)

    @staticmethod
    def _parse_evidence(raw_evidence: object) -> tuple[ApiEvidence, ...]:
        if not isinstance(raw_evidence, list):
            return ()
        parsed: list[ApiEvidence] = []
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            document_name = _first_nonempty_string(item.get("document_name"))
            chunk_id = item.get("chunk_id")
            page = _first_positive_int(item.get("page"), item.get("page_number"))
            if not isinstance(evidence_id, str) or not evidence_id or document_name is None or not isinstance(chunk_id, str) or page is None:
                continue
            section = item.get("section_path")
            parsed.append(ApiEvidence(
                evidence_id=evidence_id,
                citation_id=item.get("citation_id") if isinstance(item.get("citation_id"), str) else None,
                document_name=document_name,
                document_id=item.get("document_id") if isinstance(item.get("document_id"), str) else None,
                page=page,
                chunk_id=chunk_id,
                generation_id=item.get("generation_id") if isinstance(item.get("generation_id"), str) else None,
                section_path=tuple(str(value) for value in section) if isinstance(section, list) else (),
                excerpt=str(item.get("excerpt", ""))[:600],
                source_type=str(item.get("source_type", "initial")),
                context_role=str(item.get("context_role", "primary")),
                supports_claim_ids=tuple(value for value in item.get("supports_claim_ids", []) if isinstance(value, str)),
                completion_reason=item.get("completion_reason") if isinstance(item.get("completion_reason"), str) else None,
                relevance_label=str(item.get("relevance_label", "核心依据")),
            ))
        return tuple(parsed)

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @staticmethod
    def _raise_public_error(response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            code = body.get("code")
            message = body.get("message")
            if (
                isinstance(code, str)
                and code in _PUBLIC_ERROR_CODES
                and isinstance(message, str)
                and message.strip()
            ):
                raise ApiError(code, message.strip(), response.status_code)
        raise ApiError("UPSTREAM_UNAVAILABLE", "知识库服务暂时不可用，请稍后重试。", response.status_code)


def _first_nonempty_string(*values: object) -> str | None:
    """Return the first non-empty string, allowing P3/legacy field fallback."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_positive_int(*values: object) -> int | None:
    """Return the first positive non-boolean integer from compatible fields."""
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
