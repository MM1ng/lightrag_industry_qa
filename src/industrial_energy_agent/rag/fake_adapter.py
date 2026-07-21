"""Deterministic input-free fake for offline RAG tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from industrial_energy_agent.rag.base import (
    CitationSource,
    HealthStatus,
    IngestResult,
    RAGCallSummary,
    RAGCapabilityError,
    RAGConfiguration,
    RAGDocument,
    SearchResult,
    TrackStatus,
    VerifiedSearchMode,
)

_VERIFIED_MODES = frozenset({"local", "global", "hybrid", "naive", "mix"})


class FakeRAGAdapter:
    """Offline adapter that records counts and modes, never text or credentials."""

    def __init__(self, *, sources: Mapping[str, CitationSource] | None = None) -> None:
        self._sources = dict(sources or {})
        self._call_summaries: list[RAGCallSummary] = []
        self._ingest_counts: dict[str, int] = {}

    @property
    def call_summaries(self) -> tuple[RAGCallSummary, ...]:
        return tuple(self._call_summaries)

    def health_check(self) -> HealthStatus:
        self._call_summaries.append(RAGCallSummary(operation="health_check"))
        return HealthStatus(
            status="healthy",
            working_directory="fake://memory",
            core_version="fake",
            configuration=RAGConfiguration(
                llm_binding="fake",
                llm_model="fake",
                embedding_binding="fake",
                embedding_model="fake",
            ),
            auth_mode="fake",
        )

    def ingest_documents(self, documents: Sequence[RAGDocument]) -> IngestResult:
        count = len(documents)
        if count == 0:
            raise ValueError("At least one RAG document is required")
        track_id = f"fake-insert-{len(self._ingest_counts) + 1:04d}"
        self._ingest_counts[track_id] = count
        self._call_summaries.append(RAGCallSummary(operation="ingest_documents", input_count=count))
        return IngestResult(status="success", message="accepted", track_id=track_id)

    def track_status(self, track_id: str) -> TrackStatus:
        if track_id not in self._ingest_counts:
            raise ValueError("Unknown fake track_id")
        self._call_summaries.append(RAGCallSummary(operation="track_status"))
        return TrackStatus(
            track_id=track_id,
            documents=(),
            total_count=self._ingest_counts[track_id],
            status_summary={"PROCESSED": self._ingest_counts[track_id]},
        )

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
            raise RAGCapabilityError("Fake local filtering is not configured")
        if len(query.strip()) < 3:
            raise ValueError("RAG query must contain at least three characters")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self._call_summaries.append(RAGCallSummary(operation="search", mode=mode))
        return SearchResult(
            query=query,
            mode=mode,
            entities=(),
            relationships=(),
            chunks=(),
            references=(),
            metadata={"fake": True},
        )

    def get_sources(self, source_ids: Sequence[str]) -> list[CitationSource]:
        self._call_summaries.append(
            RAGCallSummary(operation="get_sources", input_count=len(source_ids))
        )
        return [self._sources[source_id] for source_id in source_ids if source_id in self._sources]
