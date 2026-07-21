"""HTTPX-only adapter for the locked LightRAG Server 1.5.4 REST contract."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

import httpx
from pydantic import SecretStr, ValidationError

from industrial_energy_agent.rag.base import (
    CitationSource,
    HealthStatus,
    IngestResult,
    PaginatedDocuments,
    RAGApplicationError,
    RAGCapabilityError,
    RAGConflictError,
    RAGDocument,
    RAGInvalidRequestError,
    RAGRateLimitError,
    RAGResponseError,
    RAGTrackDocument,
    RAGUnauthorizedError,
    RAGUnavailableError,
    ReconciliationResult,
    SearchResult,
    SourceResolver,
    TrackStatus,
    VerifiedSearchMode,
)

_VERIFIED_MODES = frozenset({"local", "global", "hybrid", "naive", "mix"})


class LightRAGRestAdapter:
    """Synchronous stable boundary that never imports the LightRAG package."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 0.25,
        source_resolver: SourceResolver | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be non-negative")
        parsed_url = httpx.URL(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.userinfo
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path not in {"", "/"}
        ):
            raise ValueError("LightRAG base URL must be an HTTP service origin")
        secret = api_key.get_secret_value()
        if not secret:
            raise ValueError("LightRAG API key is required")
        self._client = client or httpx.Client(
            base_url=str(parsed_url).rstrip("/"),
            timeout=timeout_seconds,
        )
        self._api_key = api_key
        self._owns_client = client is None
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._source_resolver = source_resolver

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> LightRAGRestAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _error_for_status(status_code: int) -> Exception:
        if status_code in {401, 403}:
            return RAGUnauthorizedError(
                "RAG authentication failed", retryable=False, status_code=status_code
            )
        if status_code == 409:
            return RAGConflictError(
                "RAG document conflict", retryable=False, status_code=status_code
            )
        if status_code == 429:
            return RAGRateLimitError(
                "RAG request was rate limited", retryable=True, status_code=status_code
            )
        if status_code >= 500:
            return RAGUnavailableError(
                "RAG service unavailable", retryable=True, status_code=status_code
            )
        return RAGInvalidRequestError(
            "RAG request was rejected", retryable=False, status_code=status_code
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        allow_retry: bool = True,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                request = self._client.build_request(method, path, json=json)
                request.headers.pop("Authorization", None)
                request.headers["X-API-Key"] = self._api_key.get_secret_value()
                response = self._client.send(request)
            except httpx.TransportError as error:
                if allow_retry and attempt < self._max_retries:
                    time.sleep(self._retry_base_delay_seconds * (2**attempt))
                    continue
                raise RAGUnavailableError(
                    "RAG service unavailable", retryable=True, status_code=None
                ) from error
            if response.status_code >= 400:
                normalized = self._error_for_status(response.status_code)
                if (
                    allow_retry
                    and isinstance(normalized, (RAGRateLimitError, RAGUnavailableError))
                    and attempt < self._max_retries
                ):
                    time.sleep(self._retry_base_delay_seconds * (2**attempt))
                    continue
                raise normalized
            try:
                payload = response.json()
            except ValueError as error:
                raise RAGResponseError("RAG returned an invalid JSON response") from error
            if not isinstance(payload, dict):
                raise RAGResponseError("RAG returned an invalid response envelope")
            return cast(dict[str, Any], payload)
        raise RuntimeError("unreachable retry state")

    @staticmethod
    def _require_success_envelope(payload: Mapping[str, Any]) -> None:
        if payload.get("status") == "failure":
            raise RAGApplicationError("RAG application request failed")

    @staticmethod
    def _validate(model: type[Any], payload: object) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            raise RAGResponseError("RAG returned a response outside the locked contract") from error

    def health_check(self) -> HealthStatus:
        payload = self._request("GET", "/health")
        return cast(HealthStatus, self._validate(HealthStatus, payload))

    def ingest_documents(self, documents: Sequence[RAGDocument]) -> IngestResult:
        normalized = tuple(RAGDocument.model_validate(document) for document in documents)
        if not normalized:
            raise ValueError("At least one RAG document is required")
        if len({document.file_source for document in normalized}) != len(normalized):
            raise ValueError("file_source values must be unique within an insert")
        if len(normalized) == 1:
            document = normalized[0]
            payload = self._request(
                "POST",
                "/documents/text",
                json={"text": document.text, "file_source": document.file_source},
                allow_retry=False,
            )
        else:
            payload = self._request(
                "POST",
                "/documents/texts",
                json={
                    "texts": [document.text for document in normalized],
                    "file_sources": [document.file_source for document in normalized],
                },
                allow_retry=False,
            )
        self._require_success_envelope(payload)
        return cast(IngestResult, self._validate(IngestResult, payload))

    def track_status(self, track_id: str) -> TrackStatus:
        if not track_id.strip():
            raise ValueError("track_id is required")
        payload = self._request("GET", f"/documents/track_status/{track_id}")
        return cast(TrackStatus, self._validate(TrackStatus, payload))

    def _paginated_documents(self, *, page: int) -> PaginatedDocuments:
        payload = self._request(
            "POST",
            "/documents/paginated",
            json={
                "page": page,
                "page_size": 50,
                "sort_field": "updated_at",
                "sort_direction": "desc",
            },
        )
        return cast(PaginatedDocuments, self._validate(PaginatedDocuments, payload))

    def search(
        self,
        query: str,
        *,
        mode: VerifiedSearchMode,
        top_k: int,
        local_filters: Mapping[str, str] | None = None,
    ) -> SearchResult:
        if mode not in _VERIFIED_MODES:
            raise RAGCapabilityError(f"Unsupported RAG query mode: {mode}")
        if local_filters:
            raise RAGCapabilityError(
                "Local manifest filters are not available without a filtering resolver"
            )
        if len(query.strip()) < 3:
            raise ValueError("RAG query must contain at least three characters")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        payload = self._request(
            "POST",
            "/query/data",
            json={
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "include_references": True,
                "include_chunk_content": True,
            },
        )
        self._require_success_envelope(payload)
        data = payload.get("data")
        metadata = payload.get("metadata")
        required = ("entities", "relationships", "chunks", "references")
        if (
            not isinstance(data, dict)
            or not isinstance(metadata, dict)
            or any(not isinstance(data.get(name), list) for name in required)
        ):
            raise RAGResponseError("RAG returned invalid structured query data")
        try:
            return SearchResult.model_validate(
                {
                    "query": query,
                    "mode": mode,
                    "entities": data["entities"],
                    "relationships": data["relationships"],
                    "chunks": data["chunks"],
                    "references": data["references"],
                    "metadata": metadata,
                }
            )
        except ValidationError as error:
            raise RAGResponseError("RAG returned invalid structured query data") from error

    def get_sources(self, source_ids: Sequence[str]) -> list[CitationSource]:
        if self._source_resolver is None:
            raise RAGCapabilityError("Source resolution requires a local ingestion manifest")
        resolved = self._source_resolver.resolve_sources(tuple(source_ids))
        return [CitationSource.model_validate(source) for source in resolved]

    @staticmethod
    def _matches_file_source(document: RAGTrackDocument, file_source: str) -> bool:
        return document.file_path.replace("\\", "/").rsplit("/", 1)[-1] == file_source

    def reconcile_file_source(
        self,
        file_source: str,
        *,
        track_id: str,
        expected_marker: str,
    ) -> ReconciliationResult:
        if not expected_marker.strip():
            raise ValueError("expected_marker is required")
        track = self.track_status(track_id)
        document_pages: list[PaginatedDocuments] = []
        page_number = 1
        while True:
            page = self._paginated_documents(page=page_number)
            document_pages.append(page)
            if not page.pagination.has_next:
                break
            if page_number >= page.pagination.total_pages:
                raise RAGResponseError("RAG returned inconsistent pagination metadata")
            page_number += 1
        query_result = self.search(expected_marker, mode="naive", top_k=10)
        track_match = any(
            self._matches_file_source(document, file_source)
            and document.status.casefold() == "processed"
            for document in track.documents
        )
        paginated_match = any(
            self._matches_file_source(document, file_source)
            for page in document_pages
            for document in page.documents
        )
        reference_match = any(
            str(reference.get("file_path", "")).replace("\\", "/").rsplit("/", 1)[-1] == file_source
            for reference in query_result.references
        )
        marker_match = expected_marker in json.dumps(
            query_result.chunks,
            ensure_ascii=False,
            sort_keys=True,
        )
        if track_match and paginated_match and reference_match and marker_match:
            confirmed: bool | None = True
        elif (
            not track.documents and not paginated_match and not reference_match and not marker_match
        ):
            confirmed = False
        else:
            confirmed = None
        return ReconciliationResult(
            file_source=file_source,
            confirmed=confirmed,
            probes=frozenset({"track_status", "documents_paginated", "query_references"}),
            track_match=track_match,
            paginated_match=paginated_match,
            reference_match=reference_match,
            marker_match=marker_match,
        )
