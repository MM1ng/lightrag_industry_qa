"""Immutable internal retrieval trace types for the authoritative query execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

TRACE_VERSION = "phase10a-retrieval-trace-v1"
GROUNDING_AUDIT_TRACE_VERSION = "phase10b3f-grounding-audit-v1"
FEATURE_FLAG_TRACE_VERSION = "phase10b3j-feature-flags-v2"
RUNTIME_LINEAGE_TRACE_VERSION = "phase10b3j-runtime-lineage-v2"


def feature_flag_retrieval_config(
    flags: Mapping[str, bool], config_sha256: str | None = None,
    *, include_metadata: bool = False,
) -> tuple[tuple[str, object], ...]:
    """Return a stable, secret-free trace fragment for experimental flags.

    Query execution owns whether this fragment is included.  Keeping the
    helper here lets later wiring record the exact controls without exposing
    environment values or changing the public answer contract.
    """

    entries = [(str(name), bool(flags[name])) for name in sorted(flags)]
    if include_metadata and config_sha256:
        entries.append(("feature_flag_config_sha256", config_sha256))
    if include_metadata:
        entries.append(("feature_flag_config_version", FEATURE_FLAG_TRACE_VERSION))
    return tuple(entries)


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
    # Bounded in-memory excerpt used by Phase 11 sample capture. It is
    # intentionally omitted from to_payload() so the persisted diagnostic
    # trace does not retain document content.
    content_excerpt: str = ""

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
    feature_flags: tuple[tuple[str, object], ...] = ()
    detected_model: str | None = None
    detected_component: str | None = None
    detected_parameter: str | None = None
    added_aliases: tuple[str, ...] = ()
    answer_plan: tuple[dict[str, object], ...] = ()
    completion_applied: bool = False
    completion_candidates: tuple[dict[str, object], ...] = ()
    completed_evidence: tuple[dict[str, object], ...] = ()
    coverage_requirements: tuple[str, ...] = ()
    coverage_before: tuple[str, ...] = ()
    coverage_after: tuple[str, ...] = ()
    completion_triggered: bool = False
    accepted_completion: tuple[dict[str, object], ...] = ()
    completion_context_order: tuple[str, ...] = ()
    completion_sent_to_provider: bool = False
    completion_bound_answer_points: tuple[str, ...] = ()
    completion_bound_claims: tuple[str, ...] = ()
    completion_drop_reasons: tuple[str, ...] = ()
    coverage_requirement_ids: tuple[str, ...] = ()
    coverage_funnel_stage: str = "initial"
    supplemental_retrieval_triggered: bool = False
    supplemental_query_text: str | None = None
    supplemental_query_sha256: str | None = None
    original_query_sha256: str | None = None
    normalized_query_sha256: str | None = None
    supplemental_query_different_from_normalized: bool = False
    supplemental_candidates: tuple[dict[str, object], ...] = ()
    supplemental_accepted: tuple[dict[str, object], ...] = ()
    supplemental_rejected: tuple[dict[str, object], ...] = ()
    provider_evidence_ids: tuple[str, ...] = ()
    # Runtime lineage captured around the provider boundary.  These fields are
    # deliberately internal/admin-only; the public QueryResponse remains
    # unchanged.  Empty values are meaningful when the corresponding phase is
    # disabled (for example, supplemental retrieval in the frozen baseline).
    provider_primary_evidence_ids: tuple[str, ...] = ()
    provider_completed_evidence_ids: tuple[str, ...] = ()
    provider_supplemental_evidence_ids: tuple[str, ...] = ()
    provider_context_order: tuple[str, ...] = ()
    provider_context_sha256: str | None = None
    provider_evidence_count: int = 0
    provider_context_truncated: bool = False
    provider_context_token_estimate: int | None = None
    backend_second_query_called: bool = False
    structured_citation_flag: bool = False
    json_mode_enabled: bool = False
    source_registry_count: int = 0
    source_registry_sha256: str | None = None
    requirement_registry_count: int = 0
    requirement_registry_sha256: str | None = None
    provider_raw_response_sha256: str | None = None
    parsed_structured_output_sha256: str | None = None
    structured_output_valid: bool = False
    structured_citation_fallback: bool = False
    structured_citation_fallback_mode: str | None = None
    structured_citation_fallback_reason: str | None = None
    backend_generate_call_count: int = 0
    coverage_after_parent_adjacent: tuple[str, ...] = ()
    selected_coverage: tuple[str, ...] = ()
    generated_coverage: tuple[str, ...] = ()
    grounding_retained_coverage: tuple[str, ...] = ()
    grounding_answer_point_identity: tuple[str, ...] = ()
    grounding_support_candidate_ids: tuple[dict[str, object], ...] = ()
    grounding_retained_answer_points: tuple[str, ...] = ()
    grounding_removed_answer_points: tuple[str, ...] = ()
    grounding_removal_reasons: tuple[dict[str, object], ...] = ()
    grounding_false_negative_diagnostics: tuple[dict[str, object], ...] = ()
    generated_answer_points: tuple[str, ...] = ()
    rejected_answer_points: tuple[str, ...] = ()
    support_validation_reason_codes: tuple[str, ...] = ()
    final_answer_point_ids: tuple[str, ...] = ()
    unresolved_requirement_ids: tuple[str, ...] = ()
    coverage_status: str = "uncovered"
    grounding_audit: dict[str, Any] | None = None

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
            "feature_flags": dict(self.feature_flags),
            "detected_model": self.detected_model,
            "detected_component": self.detected_component,
            "detected_parameter": self.detected_parameter,
            "added_aliases": list(self.added_aliases),
            "answer_plan": list(self.answer_plan),
            "completion_applied": self.completion_applied,
            "completion_candidates": list(self.completion_candidates),
            "completed_evidence": list(self.completed_evidence),
            "coverage_requirements": list(self.coverage_requirements),
            "coverage_before": list(self.coverage_before),
            "coverage_after": list(self.coverage_after),
            "completion_triggered": self.completion_triggered,
            "accepted_completion": list(self.accepted_completion),
            "completion_context_order": list(self.completion_context_order),
            "completion_sent_to_provider": self.completion_sent_to_provider,
            "completion_bound_answer_points": list(self.completion_bound_answer_points),
            "completion_bound_claims": list(self.completion_bound_claims),
            "completion_drop_reasons": list(self.completion_drop_reasons),
            "coverage_requirement_ids": list(self.coverage_requirement_ids),
            "coverage_funnel_stage": self.coverage_funnel_stage,
            "supplemental_retrieval_triggered": self.supplemental_retrieval_triggered,
            "supplemental_query_text": self.supplemental_query_text,
            "supplemental_query_sha256": self.supplemental_query_sha256,
            "original_query_sha256": self.original_query_sha256,
            "normalized_query_sha256": self.normalized_query_sha256,
            "supplemental_query_different_from_normalized": self.supplemental_query_different_from_normalized,
            "supplemental_candidates": list(self.supplemental_candidates),
            "supplemental_accepted": list(self.supplemental_accepted),
            "supplemental_rejected": list(self.supplemental_rejected),
            "provider_evidence_ids": list(self.provider_evidence_ids),
            "provider_primary_evidence_ids": list(self.provider_primary_evidence_ids),
            "provider_completed_evidence_ids": list(self.provider_completed_evidence_ids),
            "provider_supplemental_evidence_ids": list(self.provider_supplemental_evidence_ids),
            "provider_context_order": list(self.provider_context_order),
            "provider_context_sha256": self.provider_context_sha256,
            "provider_evidence_count": self.provider_evidence_count,
            "provider_context_truncated": self.provider_context_truncated,
            "provider_context_token_estimate": self.provider_context_token_estimate,
            "backend_second_query_called": self.backend_second_query_called,
            "structured_citation_flag": self.structured_citation_flag,
            "json_mode_enabled": self.json_mode_enabled,
            "source_registry_count": self.source_registry_count,
            "source_registry_sha256": self.source_registry_sha256,
            "requirement_registry_count": self.requirement_registry_count,
            "requirement_registry_sha256": self.requirement_registry_sha256,
            "provider_raw_response_sha256": self.provider_raw_response_sha256,
            "parsed_structured_output_sha256": self.parsed_structured_output_sha256,
            "structured_output_valid": self.structured_output_valid,
            "structured_citation_fallback": self.structured_citation_fallback,
            "structured_citation_fallback_mode": self.structured_citation_fallback_mode,
            "structured_citation_fallback_reason": self.structured_citation_fallback_reason,
            "backend_generate_call_count": self.backend_generate_call_count,
            "coverage_after_parent_adjacent": list(self.coverage_after_parent_adjacent),
            "selected_coverage": list(self.selected_coverage),
            "generated_coverage": list(self.generated_coverage),
            "grounding_retained_coverage": list(self.grounding_retained_coverage),
            "grounding_answer_point_identity": list(self.grounding_answer_point_identity),
            "grounding_support_candidate_ids": list(self.grounding_support_candidate_ids),
            "grounding_retained_answer_points": list(self.grounding_retained_answer_points),
            "grounding_removed_answer_points": list(self.grounding_removed_answer_points),
            "grounding_removal_reasons": list(self.grounding_removal_reasons),
            "grounding_false_negative_diagnostics": list(self.grounding_false_negative_diagnostics),
            "generated_answer_points": list(self.generated_answer_points),
            "rejected_answer_points": list(self.rejected_answer_points),
            "support_validation_reason_codes": list(self.support_validation_reason_codes),
            "final_answer_point_ids": list(self.final_answer_point_ids),
            "unresolved_requirement_ids": list(self.unresolved_requirement_ids),
            "coverage_status": self.coverage_status,
            "grounding_audit": self.grounding_audit,
        }
