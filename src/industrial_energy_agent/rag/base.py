"""Vendor-neutral RAG contracts and normalized failure types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

VerifiedSearchMode: TypeAlias = Literal["local", "global", "hybrid", "naive", "mix"]
JsonObject: TypeAlias = dict[str, JsonValue]


class RAGRequestError(RuntimeError):
    """Normalized REST failure that never exposes an upstream response body."""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class RAGUnauthorizedError(RAGRequestError):
    """The internal LightRAG credential was rejected."""


class RAGConflictError(RAGRequestError):
    """The remote operation conflicts with existing state."""


class RAGInvalidRequestError(RAGRequestError):
    """The stable adapter generated a request rejected by the server."""


class RAGRateLimitError(RAGRequestError):
    """The LightRAG service or one of its dependencies is rate limited."""


class RAGUnavailableError(RAGRequestError):
    """The LightRAG service is unavailable or timed out."""


class RAGApplicationError(RuntimeError):
    """HTTP succeeded but the LightRAG response envelope reports failure."""


class RAGResponseError(RuntimeError):
    """The LightRAG response does not satisfy the locked contract."""


class RAGCapabilityError(ValueError):
    """A caller requested a capability not verified in the locked server."""


class RAGDocument(BaseModel):
    """Text document accepted by the stable ingestion boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    text: str = Field(min_length=1)
    file_source: str = Field(min_length=1, max_length=255)

    @field_validator("file_source")
    @classmethod
    def require_file_source_basename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("file_source must be a basename without directory components")
        return value


class IngestResult(BaseModel):
    """Normalized asynchronous insert acknowledgement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "partial_success"]
    message: str
    track_id: str = Field(min_length=1)


class RAGTrackDocument(BaseModel):
    """Captured LightRAG 1.5.4 document status shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    content_summary: str
    content_length: int = Field(ge=0)
    status: str
    created_at: str
    updated_at: str
    track_id: str | None = None
    chunks_count: int | None = Field(default=None, ge=0)
    error_msg: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    file_path: str

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_nullable_metadata(cls, value: object) -> object:
        return {} if value is None else value


class TrackStatus(BaseModel):
    """Normalized status for an asynchronous insert track."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    track_id: str
    documents: tuple[RAGTrackDocument, ...]
    total_count: int = Field(ge=0)
    status_summary: dict[str, int]


class PaginationInfo(BaseModel):
    """Captured pagination envelope used during reconciliation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    has_next: bool
    has_prev: bool


class PaginatedDocuments(BaseModel):
    """Captured document page used for remote-side reconciliation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    documents: tuple[RAGTrackDocument, ...]
    pagination: PaginationInfo
    status_counts: dict[str, int]


class RAGConfiguration(BaseModel):
    """Non-sensitive health configuration safe for business code and traces."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    llm_binding: str
    llm_model: str
    embedding_binding: str
    embedding_model: str


class HealthStatus(BaseModel):
    """Safe subset of health data that discards hosts and tenant identifiers."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    working_directory: str
    core_version: str
    configuration: RAGConfiguration
    auth_mode: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status.casefold() == "healthy"


class SearchResult(BaseModel):
    """Raw retrieval evidence returned without a second generated answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    mode: VerifiedSearchMode
    entities: tuple[JsonObject, ...]
    relationships: tuple[JsonObject, ...]
    chunks: tuple[JsonObject, ...]
    references: tuple[JsonObject, ...]
    metadata: JsonObject


class CitationSource(BaseModel):
    """Locally resolved source metadata for a LightRAG reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str = Field(min_length=1)
    file_source: str = Field(min_length=1)
    document_id: str | None = None
    chunk_ids: tuple[str, ...] = ()


class RAGCallSummary(BaseModel):
    """Input-free metadata safe to expose in deterministic offline traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(min_length=1)
    input_count: int = Field(default=1, ge=0)
    mode: VerifiedSearchMode | None = None


class ReconciliationResult(BaseModel):
    """Tri-state result from all probes required after an ambiguous insert."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_source: str
    confirmed: bool | None
    probes: frozenset[str]
    track_match: bool
    paginated_match: bool
    reference_match: bool
    marker_match: bool = False


class SourceResolver(Protocol):
    """Local manifest boundary because LightRAG 1.5.4 has no sources endpoint."""

    def resolve_sources(self, source_ids: tuple[str, ...]) -> Sequence[CitationSource]: ...


class RAGAdapter(Protocol):
    """Stable synchronous retrieval boundary used by business services."""

    def health_check(self) -> HealthStatus: ...

    def ingest_documents(self, documents: Sequence[RAGDocument]) -> IngestResult: ...

    def track_status(self, track_id: str) -> TrackStatus: ...

    def reconcile_file_source(
        self,
        file_source: str,
        *,
        track_id: str,
        expected_marker: str,
    ) -> ReconciliationResult: ...

    def search(
        self,
        query: str,
        *,
        mode: VerifiedSearchMode,
        top_k: int,
        local_filters: Mapping[str, str] | None = None,
    ) -> SearchResult: ...

    def get_sources(self, source_ids: Sequence[str]) -> list[CitationSource]: ...
