"""Immutable internal retrieval trace types for the authoritative query execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

TRACE_VERSION = "phase10a-retrieval-trace-v1"


@dataclass(frozen=True, slots=True)
class RetrievalTraceItem:
    initial_rank: int
    initial_score: float | None
    retrieval_source: str
    document_id: str | None
    document_name: str
    page_number: int
    chunk_id: str
    section_path: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    reranked_rank: int | None = None
    reranked_score: float | None = None
    used_for_answer: bool = False
    cited_in_answer: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "initial_rank": self.initial_rank,
            "initial_score": self.initial_score,
            "retrieval_source": self.retrieval_source,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "page_number": self.page_number,
            "chunk_id": self.chunk_id,
            "section_path": list(self.section_path),
            "matched_terms": list(self.matched_terms),
            "reranked_rank": self.reranked_rank,
            "reranked_score": self.reranked_score,
            "used_for_answer": self.used_for_answer,
            "cited_in_answer": self.cited_in_answer,
        }


@dataclass(frozen=True, slots=True)
class SelectedEvidenceTrace:
    final_rank: int
    chunk_id: str
    document_id: str | None
    document_name: str
    page_number: int
    initial_rank: int | None
    reranked_rank: int | None
    used_for_answer: bool
    cited_in_answer: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "final_rank": self.final_rank,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "page_number": self.page_number,
            "initial_rank": self.initial_rank,
            "reranked_rank": self.reranked_rank,
            "used_for_answer": self.used_for_answer,
            "cited_in_answer": self.cited_in_answer,
        }


@dataclass(frozen=True, slots=True)
class RetrievalExecutionTrace:
    trace_version: str
    original_query: str
    normalized_query: str
    retrieval_config: tuple[tuple[str, object], ...]
    initial_results: tuple[RetrievalTraceItem, ...]
    rerank_applied: bool
    reranked_results: tuple[RetrievalTraceItem, ...]
    final_selected_chunks: tuple[SelectedEvidenceTrace, ...]
    selected_chunk_ids: tuple[str, ...]
    normalization_ms: float
    retrieval_ms: float
    rerank_ms: float
    evidence_selection_ms: float
    detected_model: str | None = None
    detected_component: str | None = None
    detected_parameter: str | None = None
    added_aliases: tuple[str, ...] = ()
    answer_plan: tuple[dict[str, object], ...] = ()

    def with_document_ids(self, document_ids: Mapping[str, str]) -> RetrievalExecutionTrace:
        return replace(
            self,
            initial_results=tuple(
                replace(item, document_id=document_ids.get(item.document_name))
                for item in self.initial_results
            ),
            reranked_results=tuple(
                replace(item, document_id=document_ids.get(item.document_name))
                for item in self.reranked_results
            ),
            final_selected_chunks=tuple(
                replace(item, document_id=document_ids.get(item.document_name))
                for item in self.final_selected_chunks
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "trace_version": self.trace_version,
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "retrieval_config": dict(self.retrieval_config),
            "initial_results": [item.to_payload() for item in self.initial_results],
            "rerank_applied": self.rerank_applied,
            "reranked_results": [item.to_payload() for item in self.reranked_results],
            "final_selected_chunks": [
                item.to_payload() for item in self.final_selected_chunks
            ],
            "normalization_ms": self.normalization_ms,
            "retrieval_ms": self.retrieval_ms,
            "rerank_ms": self.rerank_ms,
            "evidence_selection_ms": self.evidence_selection_ms,
            "detected_model": self.detected_model,
            "detected_component": self.detected_component,
            "detected_parameter": self.detected_parameter,
            "added_aliases": list(self.added_aliases),
            "answer_plan": list(self.answer_plan),
        }
