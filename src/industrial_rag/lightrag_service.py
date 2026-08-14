"""Small service around the verified official LightRAG 1.5.4 async API."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, Protocol, cast

from industrial_rag.answer_grounding import (
    AnswerPoint,
    GroundedAnswer,
    GroundingAudit,
    build_answer_plan,
    build_non_generation_audit,
    classify_question_type,
)
from industrial_rag.citation_formatter import Citation, collect_citations, encode_chunk_header
from industrial_rag.conditional_completion import plan_conditional_completion
from industrial_rag.config import (
    SUPPORTED_QUERY_MODES,
    Settings,
    check_storage_compatibility,
    write_storage_metadata,
)
from industrial_rag.document_parser import DocumentChunk
from industrial_rag.evidence_answer_schema import EvidenceRef, StructuredAnswerPoint
from industrial_rag.evidence_completion import ContextRecord
from industrial_rag.evidence_policy import (
    EvidenceCandidate,
    _tokens,
    select_evidence,
    select_partial_evidence,
)
from industrial_rag.post_retrieval_recovery import evaluate_post_retrieval_recovery
from industrial_rag.query_normalization import NormalizationResult, normalize_query
from industrial_rag.retrieval_trace import (
    GROUNDING_AUDIT_TRACE_VERSION,
    RUNTIME_LINEAGE_TRACE_VERSION,
    TRACE_VERSION,
    RetrievalExecutionTrace,
    RetrievalTraceItem,
    SelectedEvidenceTrace,
    feature_flag_retrieval_config,
)
from industrial_rag.structured_citation_output import (
    RequirementRegistry,
    SourceRegistry,
    StructuredCitationDecision,
    render_public_citation_numbers,
    validate_structured_citation_output,
)
from industrial_rag.structured_generation_policy import validate_answer_points
from industrial_rag.supplemental_retrieval_policy import run_supplemental_retrieval
from industrial_rag.supplemental_retrieval_policy import (
    supplemental_query_sha256 as hash_supplemental_query,
)
from industrial_rag.vector_collections import VectorBackend

QueryMode = Literal["mix", "hybrid", "local", "global", "naive"]
INSUFFICIENT_EVIDENCE_MESSAGE = "手册中未检索到充分依据，无法可靠回答该问题。"
logger = logging.getLogger(__name__)
_SYSTEM_PROMPT_BASE = (
    "你是工业离心泵手册问答助手。只能依据检索到的手册内容回答；"
    f"依据不足时必须原样回答：{INSUFFICIENT_EVIDENCE_MESSAGE} "
    "不要猜测、补写或编造文件名和页码。\n\n"
)
_SELECTED_CONTEXT_LABEL = "以下是已筛选的手册证据：\n"
_CHUNK_BOUNDARY = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"


@dataclass(frozen=True, slots=True)
class QueryOptions:
    mode: QueryMode
    top_k: int = 12
    chunk_top_k: int = 20
    enable_rerank: bool = False


@dataclass(frozen=True, slots=True)
class QueryResult:
    answer: str
    citations: tuple[Citation, ...]
    mode: QueryMode
    retrieval_chunk_ids: tuple[str, ...] = ()
    retrieval_meta: tuple[tuple[str, str, int], ...] = ()
    retrieval_trace: RetrievalExecutionTrace | None = None
    answer_status: Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"] = "success"
    answer_points: tuple[AnswerPoint, ...] = ()
    grounding_failure_categories: tuple[str, ...] = ()


class LightRAGBackend(Protocol):
    async def initialize_storages(self) -> None: ...

    async def finalize_storages(self) -> None: ...

    async def ainsert(self, input: list[str], **kwargs: object) -> str: ...

    async def get_track_status(self, track_id: str) -> dict[str, str]: ...

    async def aquery_data(self, query: str, param: QueryOptions) -> dict[str, object]: ...

    async def generate(
        self,
        question: str,
        context: str,
        system_prompt: str,
        *,
        response_format: dict[str, str] | None = None,
    ) -> str: ...


class _OfficialBackend:
    def __init__(
        self,
        rag: Any,
        query_param_type: type[Any],
        llm_model_func: Callable[..., Awaitable[str]],
    ) -> None:
        self._rag = rag
        self._query_param_type = query_param_type
        self._llm_model_func = llm_model_func

    def _param(self, value: QueryOptions) -> Any:
        return self._query_param_type(
            mode=value.mode,
            top_k=value.top_k,
            chunk_top_k=value.chunk_top_k,
            enable_rerank=value.enable_rerank,
        )

    async def initialize_storages(self) -> None:
        await self._rag.initialize_storages()

    async def finalize_storages(self) -> None:
        await self._rag.finalize_storages()

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        return cast(str, await self._rag.ainsert(input=input, **kwargs))

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        documents = await self._rag.aget_docs_by_track_id(track_id)
        return {
            doc_id: str(getattr(document.status, "value", document.status)).casefold()
            for doc_id, document in documents.items()
        }

    async def aquery_data(self, query: str, param: QueryOptions) -> dict[str, object]:
        return cast(dict[str, object], await self._rag.aquery_data(query, self._param(param)))

    async def generate(
        self,
        question: str,
        context: str,
        system_prompt: str,
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        options = {"response_format": response_format} if response_format else {}
        result = await self._llm_model_func(question, system_prompt=system_prompt, **options)
        if not isinstance(result, str):
            raise RuntimeError("LightRAG LLM returned a streaming response unexpectedly")
        return result


def _register_project_qdrant_storage() -> None:
    """Register the project storage before LightRAG validates its backend name."""
    from lightrag.kg import STORAGE_ENV_REQUIREMENTS, STORAGE_IMPLEMENTATIONS, STORAGES

    storage_name = "PhysicalQdrantVectorDBStorage"
    implementations = STORAGE_IMPLEMENTATIONS["VECTOR_STORAGE"]["implementations"]
    if storage_name not in implementations:
        implementations.append(storage_name)
    STORAGES[storage_name] = "industrial_rag.physical_qdrant_storage"
    STORAGE_ENV_REQUIREMENTS[storage_name] = []


def build_official_backend(
    settings: Settings,
    *,
    llm_model_func: Callable[..., Awaitable[str]] | None = None,
) -> LightRAGBackend:
    """Build against the locally installed HKUDS LightRAG API, with explicit 1024 dimensions.

    ``llm_model_func`` is an optional caller-supplied LLM implementation used
    by experiments that must record usage and enforce a single fixed model.
    When omitted, the built-in model chain is used (respecting
    ``settings.model_fallback_enabled``).
    """

    try:
        from lightrag import LightRAG, QueryParam
        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
        from lightrag.utils import EmbeddingFunc
    except ImportError as error:
        raise RuntimeError("未安装官方 lightrag-hku；请按 requirements.txt 安装依赖") from error

    if settings.vector_backend is VectorBackend.qdrant:
        _register_project_qdrant_storage()
        if settings.qdrant_generation is None:
            raise ValueError("Qdrant backend requires an active generation")

    if llm_model_func is None:
        active_model_index = 0

        async def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> str:
            nonlocal active_model_index
            kwargs.pop("model", None)
            configured_models = (
                settings.llm_models
                if settings.model_fallback_enabled
                else (settings.llm_model,)
            )
            for model_index in range(active_model_index, len(configured_models)):
                model = configured_models[model_index]
                try:
                    response = await openai_complete_if_cache(
                        model=model,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        history_messages=history_messages or [],
                        base_url=settings.llm_base_url,
                        api_key=settings.api_key,
                        **kwargs,
                    )
                except Exception as error:
                    if (
                        not _is_model_failover_error(error)
                        or model_index == len(configured_models) - 1
                    ):
                        raise
                    logger.warning(
                        "DashScope model %s unavailable; trying configured fallback model.",
                        model,
                    )
                    continue
                active_model_index = model_index
                return response
            raise RuntimeError("所有配置的 DashScope 模型均不可用")

    embedding_func = EmbeddingFunc(
        embedding_dim=settings.embedding_dim,
        max_token_size=8192,
        func=partial(
            openai_embed.func,
            model=settings.embedding_model,
            base_url=settings.llm_base_url,
            api_key=settings.api_key,
        ),
        send_dimensions=True,
        model_name=settings.embedding_model,
        supports_asymmetric=True,
    )
    rag = LightRAG(
        working_dir=str(settings.working_dir),
        llm_model_func=llm_model_func,
        llm_model_name=settings.llm_model,
        embedding_func=embedding_func,
        chunk_token_size=settings.chunk_token_size,
        enable_llm_cache=settings.enable_llm_cache,
        enable_content_headings=True,
        entity_extract_max_gleaning=0,
        entity_extract_max_records=12,
        entity_extract_max_entities=12,
        max_parallel_insert=1,
        vector_storage=(
            "PhysicalQdrantVectorDBStorage"
            if settings.vector_backend is VectorBackend.qdrant
            else "NanoVectorDBStorage"
        ),
        workspace=settings.vector_workspace or "",
        vector_db_storage_cls_kwargs=(
            {
                "qdrant_collection_prefix": settings.qdrant_collection_prefix,
                "qdrant_generation": settings.qdrant_generation,
                "qdrant_kb_id": settings.qdrant_kb_id,
                "qdrant_url": settings.qdrant_url,
                "qdrant_api_key": settings.qdrant_api_key,
            }
            if settings.vector_backend is VectorBackend.qdrant
            else {}
        ),
    )
    return _OfficialBackend(rag, QueryParam, llm_model_func)


def _is_model_failover_error(error: Exception) -> bool:
    """Limit automatic model changes to provider capacity/availability failures."""

    status_code = getattr(error, "status_code", None)
    if status_code == 429:
        return True
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "model unavailable",
            "model not available",
            "model does not exist",
            "model_not_found",
        )
    )


def _selected_context(selected: Sequence[EvidenceCandidate]) -> str:
    return "\n\n".join(
        f"{encode_chunk_header(candidate.citation)}\n{candidate.text}" for candidate in selected
    )


def _extract_retrieved(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract retrieved chunk identities from a LightRAG evidence payload."""
    data = evidence.get("data", {}) if isinstance(evidence, dict) else {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for field in ("chunks", "references"):
        values = data.get(field, []) if isinstance(data, dict) else []
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            citations = collect_citations({"data": {"references": [], "chunks": [value]}})
            if not citations:
                continue
            citation = citations[0]
            identity = (citation.source_file, citation.page_number, citation.chunk_id)
            if identity in seen:
                continue
            seen.add(identity)
            score = value.get("score")
            if score is None:
                score = value.get("distance")
            retrieval_source = value.get("retrieval_source")
            if not isinstance(retrieval_source, str) or not retrieval_source.strip():
                retrieval_source = "lightrag_mix_unspecified"
            section_path = value.get("section_path", ())
            if not isinstance(section_path, (list, tuple)):
                section_path = ()
            out.append(
                {
                    "file": citation.source_file,
                    "page": citation.page_number,
                    "chunk_id": citation.chunk_id,
                    "score": score if isinstance(score, (int, float)) else None,
                    "rank": len(out) + 1,
                    "retrieval_source": retrieval_source,
                    "section_path": tuple(str(part) for part in section_path if str(part)),
                    "content": value.get("content") if isinstance(value.get("content"), str) else "",
                }
            )
    return out


def _build_retrieval_trace(
    *,
    original_query: str,
    normalized_query: str,
    options: QueryOptions,
    retrieved: list[dict[str, Any]],
    selected: Sequence[EvidenceCandidate],
    cited: Sequence[Citation],
    normalization_ms: float,
    retrieval_ms: float,
    evidence_selection_ms: float,
    feature_flags: Sequence[tuple[str, object]] = (),
    normalization: NormalizationResult | None = None,
    answer_plan: Sequence[AnswerPoint] = (),
    grounding_audit: GroundingAudit | None = None,
    completion_candidates: Sequence[dict[str, object]] = (),
    completed_evidence: Sequence[dict[str, object]] = (),
    completion_applied: bool = False,
    coverage_requirements: Sequence[str] = (),
    coverage_before: Sequence[str] = (),
    coverage_after: Sequence[str] = (),
    completion_triggered: bool = False,
    accepted_completion: Sequence[dict[str, object]] = (),
    completion_context_order: Sequence[str] = (),
    completion_sent_to_provider: bool = False,
    completion_bound_answer_points: Sequence[str] = (),
    completion_bound_claims: Sequence[str] = (),
    completion_drop_reasons: Sequence[str] = (),
    coverage_requirement_ids: Sequence[str] = (),
    coverage_funnel_stage: str = "initial",
    supplemental_retrieval_triggered: bool = False,
    supplemental_query_text: str | None = None,
    supplemental_query_sha256: str | None = None,
    supplemental_query_different_from_normalized: bool = False,
    supplemental_candidates: Sequence[dict[str, object]] = (),
    supplemental_accepted: Sequence[dict[str, object]] = (),
    supplemental_rejected: Sequence[dict[str, object]] = (),
    provider_evidence_ids: Sequence[str] = (),
    generated_answer_points: Sequence[str] = (),
    rejected_answer_points: Sequence[str] = (),
    support_validation_reason_codes: Sequence[str] = (),
    final_answer_point_ids: Sequence[str] = (),
    unresolved_requirement_ids: Sequence[str] = (),
    provider_primary_evidence_ids: Sequence[str] = (),
    provider_completed_evidence_ids: Sequence[str] = (),
    provider_supplemental_evidence_ids: Sequence[str] = (),
    provider_context_order: Sequence[str] = (),
    provider_contexts: Sequence[str] = (),
    provider_context_sha256: str | None = None,
    provider_evidence_count: int = 0,
    provider_context_truncated: bool = False,
    provider_context_token_estimate: int | None = None,
    backend_second_query_called: bool = False,
    coverage_after_parent_adjacent: Sequence[str] = (),
    selected_coverage: Sequence[str] = (),
    generated_coverage: Sequence[str] = (),
    grounding_retained_coverage: Sequence[str] = (),
    grounding_answer_point_identity: Sequence[str] = (),
    grounding_support_candidate_ids: Sequence[dict[str, object]] = (),
    grounding_retained_answer_points: Sequence[str] = (),
    grounding_removed_answer_points: Sequence[str] = (),
    grounding_removal_reasons: Sequence[dict[str, object]] = (),
    grounding_false_negative_diagnostics: Sequence[dict[str, object]] = (),
    structured_citation_flag: bool = False,
    json_mode_enabled: bool = False,
    source_registry_count: int = 0,
    source_registry_sha256: str | None = None,
    requirement_registry_count: int = 0,
    requirement_registry_sha256: str | None = None,
    provider_raw_response_sha256: str | None = None,
    parsed_structured_output_sha256: str | None = None,
    structured_output_valid: bool = False,
    structured_citation_fallback: bool = False,
    structured_citation_fallback_mode: str | None = None,
    structured_citation_fallback_reason: str | None = None,
    backend_generate_call_count: int = 0,
) -> RetrievalExecutionTrace:
    selected_identities = {
        (item.citation.source_file, item.citation.page_number, item.citation.chunk_id)
        for item in selected
    }
    cited_identities = {
        (item.source_file, item.page_number, item.chunk_id) for item in cited
    }
    question_terms = _tokens(normalized_query)
    initial_results: list[RetrievalTraceItem] = []
    ranks_by_identity: dict[tuple[str, int, str], int] = {}
    for item in retrieved:
        identity = (item["file"], item["page"], item["chunk_id"])
        ranks_by_identity[identity] = item["rank"]
        candidate_terms = _tokens(item["content"])
        initial_results.append(
            RetrievalTraceItem(
                initial_rank=item["rank"],
                initial_score=item["score"],
                retrieval_source=item["retrieval_source"],
                document_id=None,
                document_name=item["file"],
                page_number=item["page"],
                chunk_id=item["chunk_id"],
                section_path=item["section_path"],
                matched_terms=tuple(sorted(question_terms & candidate_terms)),
                used_for_answer=identity in selected_identities,
                cited_in_answer=identity in cited_identities,
                content_excerpt=str(item.get("content") or "")[:240],
            )
        )
    final_selected = tuple(
        SelectedEvidenceTrace(
            final_rank=final_rank,
            chunk_id=item.citation.chunk_id,
            document_id=None,
            document_name=item.citation.source_file,
            page_number=item.citation.page_number,
            initial_rank=ranks_by_identity.get(
                (item.citation.source_file, item.citation.page_number, item.citation.chunk_id)
            ),
            reranked_rank=None,
            used_for_answer=True,
            cited_in_answer=(
                item.citation.source_file,
                item.citation.page_number,
                item.citation.chunk_id,
            )
            in cited_identities,
        )
        for final_rank, item in enumerate(selected, start=1)
    )
    return RetrievalExecutionTrace(
        # Keep the Phase 10A trace contract for callers that have not enabled
        # the runtime grounding/audit pipeline.  The 10B-3J lineage version is
        # emitted only for the real audited query path, where the provider
        # boundary fields are meaningful rather than synthetic defaults.
        trace_version=(
            RUNTIME_LINEAGE_TRACE_VERSION
            if grounding_audit is not None and (provider_evidence_ids or coverage_requirements)
            else (GROUNDING_AUDIT_TRACE_VERSION if grounding_audit is not None else TRACE_VERSION)
        ),
        original_query=original_query,
        normalized_query=normalized_query,
        retrieval_query=normalized_query,
        retrieval_config=(
            ("mode", options.mode),
            ("top_k", options.top_k),
            ("chunk_top_k", options.chunk_top_k),
            ("rerank_enabled", options.enable_rerank),
            *feature_flags,
        ),
        initial_results=tuple(initial_results),
        rerank_applied=False,
        reranked_results=(),
        final_selected_chunks=final_selected,
        selected_chunk_ids=tuple(item.chunk_id for item in final_selected),
        normalization_ms=normalization_ms,
        retrieval_ms=retrieval_ms,
        rerank_ms=0.0,
        evidence_selection_ms=evidence_selection_ms,
        feature_flags=tuple(feature_flags),
        detected_model=normalization.detected_model if normalization else None,
        detected_component=normalization.detected_component if normalization else None,
        detected_parameter=normalization.detected_parameter if normalization else None,
        added_aliases=normalization.added_aliases if normalization else (),
        answer_plan=tuple(item.to_payload() for item in answer_plan),
        grounding_audit=(grounding_audit.to_payload() if grounding_audit is not None else None),
        completion_applied=completion_applied,
        completion_candidates=tuple(completion_candidates),
        completed_evidence=tuple(completed_evidence),
        coverage_requirements=tuple(coverage_requirements),
        coverage_before=tuple(coverage_before),
        coverage_after=tuple(coverage_after),
        completion_triggered=completion_triggered,
        accepted_completion=tuple(accepted_completion),
        completion_context_order=tuple(completion_context_order),
        completion_sent_to_provider=completion_sent_to_provider,
        completion_bound_answer_points=tuple(completion_bound_answer_points),
        completion_bound_claims=tuple(completion_bound_claims),
        completion_drop_reasons=tuple(completion_drop_reasons),
        coverage_requirement_ids=tuple(coverage_requirement_ids),
        coverage_funnel_stage=coverage_funnel_stage,
        supplemental_retrieval_triggered=supplemental_retrieval_triggered,
        supplemental_query_text=supplemental_query_text,
        supplemental_query_sha256=supplemental_query_sha256,
        original_query_sha256=hashlib.sha256(original_query.encode("utf-8")).hexdigest(),
        normalized_query_sha256=hashlib.sha256(normalized_query.encode("utf-8")).hexdigest(),
        supplemental_query_different_from_normalized=supplemental_query_different_from_normalized,
        supplemental_candidates=tuple(supplemental_candidates),
        supplemental_accepted=tuple(supplemental_accepted),
        supplemental_rejected=tuple(supplemental_rejected),
        provider_evidence_ids=tuple(provider_evidence_ids),
        provider_primary_evidence_ids=tuple(provider_primary_evidence_ids),
        provider_completed_evidence_ids=tuple(provider_completed_evidence_ids),
        provider_supplemental_evidence_ids=tuple(provider_supplemental_evidence_ids),
        provider_context_order=tuple(provider_context_order),
        provider_contexts=tuple(provider_contexts),
        provider_context_sha256=provider_context_sha256,
        provider_evidence_count=provider_evidence_count,
        provider_context_truncated=provider_context_truncated,
        provider_context_token_estimate=provider_context_token_estimate,
        backend_second_query_called=backend_second_query_called,
        structured_citation_flag=structured_citation_flag,
        json_mode_enabled=json_mode_enabled,
        source_registry_count=source_registry_count,
        source_registry_sha256=source_registry_sha256,
        requirement_registry_count=requirement_registry_count,
        requirement_registry_sha256=requirement_registry_sha256,
        provider_raw_response_sha256=provider_raw_response_sha256,
        parsed_structured_output_sha256=parsed_structured_output_sha256,
        structured_output_valid=structured_output_valid,
        structured_citation_fallback=structured_citation_fallback,
        structured_citation_fallback_mode=structured_citation_fallback_mode,
        structured_citation_fallback_reason=structured_citation_fallback_reason,
        backend_generate_call_count=backend_generate_call_count,
        coverage_after_parent_adjacent=tuple(coverage_after_parent_adjacent),
        selected_coverage=tuple(selected_coverage),
        generated_coverage=tuple(generated_coverage),
        grounding_retained_coverage=tuple(grounding_retained_coverage),
        grounding_answer_point_identity=tuple(grounding_answer_point_identity),
        grounding_support_candidate_ids=tuple(grounding_support_candidate_ids),
        grounding_retained_answer_points=tuple(grounding_retained_answer_points),
        grounding_removed_answer_points=tuple(grounding_removed_answer_points),
        grounding_removal_reasons=tuple(grounding_removal_reasons),
        grounding_false_negative_diagnostics=tuple(grounding_false_negative_diagnostics),
        generated_answer_points=tuple(generated_answer_points),
        rejected_answer_points=tuple(rejected_answer_points),
        support_validation_reason_codes=tuple(support_validation_reason_codes),
        final_answer_point_ids=tuple(final_answer_point_ids),
        unresolved_requirement_ids=tuple(unresolved_requirement_ids),
    )


def _generation_system_prompt(context: str) -> str:
    return _SYSTEM_PROMPT_BASE + _SELECTED_CONTEXT_LABEL + context


def _structured_citation_system_prompt(
    registry: SourceRegistry,
    requirements: RequirementRegistry,
) -> str:
    """Build the exact child-only provider context for structured citations."""

    sources = "\n\n".join(
        "\n".join(
            (
                f"Source {entry.source_id}",
                f"document_name={entry.evidence.document_name}",
                f"child_chunk_id={entry.evidence.chunk_id}",
                f"generation_id={entry.evidence.generation_id}",
                f"content={entry.evidence.text}",
            )
        )
        for entry in registry.entries
    )
    unresolved = ", ".join(
        f"{entry.requirement_id}:{entry.label}" for entry in requirements.entries
    ) or "(none)"
    return (
        _SYSTEM_PROMPT_BASE
        + "请只输出合法JSON，不要输出Markdown代码块或其他文字。\n"
        + "只能使用下列 Source ID；不要输出数据库 Chunk ID。每个 answer_point 必须有 1 到 2 个不同 source_ids。\n"
        + "Source 足够时不要添加第二个；没有直接证据的答案点不要输出。\n"
        + "若有答案点且有未解决项，使用 partial_answer；没有答案点时使用 insufficient_evidence。\n"
        + "输出格式：{\"status\":\"success|partial_answer|insufficient_evidence\",\"answer_points\":[{\"text\":\"...\",\"source_ids\":[\"S1\"]}],\"unresolved_requirement_ids\":[\"R1\"]}\n"
        + f"当前 Requirement IDs：{unresolved}\n\n"
        + "以下是可直接公开引用的 Child Sources：\n"
        + sources
    )


def _phase10b3i_trace_flags(settings: Settings) -> tuple[tuple[str, object], ...]:
    return feature_flag_retrieval_config(
        settings.phase10b3i_feature_flags,
        settings.phase10b3i_config_sha256,
        include_metadata=True,
    )


def _load_context_registry(settings: Settings) -> dict[str, ContextRecord]:
    """Load the immutable generation registry; never scan source PDFs at query time."""
    candidates = (
        settings.working_dir.parent / "context_registry" / "chunks.jsonl",
        settings.working_dir / "context_registry" / "chunks.jsonl",
    )
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        return {}
    records: dict[str, ContextRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        records[str(row["chunk_id"])] = ContextRecord(
            knowledge_base_id=str(row.get("knowledge_base_id") or settings.qdrant_kb_id or ""),
            generation_id=str(row.get("generation_id") or settings.qdrant_generation or ""),
            document_id=str(row.get("document_id") or ""),
            document_name=str(row.get("document_name") or ""),
            chunk_id=str(row["chunk_id"]),
            text=str(row.get("content") or ""),
            page_start=int(row.get("page_start") or 1),
            section_path=tuple(str(item) for item in row.get("section_path", ()) or ()),
            parent_chunk_id=row.get("parent_chunk_id"),
            previous_chunk_id=row.get("previous_chunk_id"),
            next_chunk_id=row.get("next_chunk_id"),
            table_id=row.get("table_id"),
            table_header_chunk_id=row.get("table_header_chunk_id"),
        )
    parent_path = path.with_name("parents.jsonl")
    if parent_path.is_file():
        for line in parent_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            parent_id = str(row["parent_chunk_id"])
            records[parent_id] = ContextRecord(
                knowledge_base_id=str(row.get("knowledge_base_id") or settings.qdrant_kb_id or ""),
                generation_id=str(row.get("generation_id") or settings.qdrant_generation or ""),
                document_id=str(row.get("document_id") or ""),
                document_name=str(row.get("document_name") or ""),
                chunk_id=parent_id,
                text=str(row.get("content") or ""),
                page_start=int(row.get("page_start") or 1),
                section_path=tuple(str(item) for item in row.get("section_path", ()) or ()),
            )
    return records


class LightRAGService:
    def __init__(self, settings: Settings, *, backend: LightRAGBackend | None = None) -> None:
        self.settings = settings
        self._backend: LightRAGBackend | None = backend
        self._initialized = False

    async def initialize(self) -> None:
        check_storage_compatibility(
            self.settings.working_dir,
            self.settings.embedding_model,
            self.settings.embedding_dim,
        )
        if self._backend is None:
            self._backend = build_official_backend(self.settings)
        await self._backend.initialize_storages()
        write_storage_metadata(
            self.settings.working_dir,
            self.settings.embedding_model,
            self.settings.embedding_dim,
        )
        self._initialized = True

    async def close(self) -> None:
        if self._initialized:
            await self._backend.finalize_storages()
            self._initialized = False

    async def ingest(self, chunks: Sequence[DocumentChunk]) -> str:
        if not self._initialized:
            raise RuntimeError("LightRAG 尚未初始化")
        if not chunks:
            raise ValueError("没有可导入的文档块")
        by_source: dict[str, list[DocumentChunk]] = {}
        for chunk in chunks:
            by_source.setdefault(chunk.source_file, []).append(chunk)
        last_track_id = ""
        for source_file, source_chunks in by_source.items():
            rendered_chunks: list[str] = []
            for chunk in source_chunks:
                section = chunk.section_title or "未识别章节"
                citation = Citation(chunk.source_file, chunk.page_number, chunk.chunk_id)
                rendered_chunks.append(
                    f"{encode_chunk_header(citation)}\n"
                    f"[来源：{chunk.source_file}，第{chunk.page_number}页，章节：{section}]\n"
                    f"{chunk.text}"
                )
            identity = hashlib.sha256(
                "\n".join(chunk.chunk_id for chunk in source_chunks).encode("utf-8")
            ).hexdigest()[:20]
            last_track_id = await self._backend.ainsert(
                input=[_CHUNK_BOUNDARY.join(rendered_chunks)],
                ids=[f"manual-{identity}"],
                file_paths=[source_file],
                split_by_character=_CHUNK_BOUNDARY,
                split_by_character_only=True,
            )
            statuses = await self._backend.get_track_status(last_track_id)
            if not statuses or not all(
                s == "processed" or doc_id.startswith("dup-") for doc_id, s in statuses.items()
            ):
                raise RuntimeError(
                    f"手册 {source_file} 导入失败，LightRAG 状态: {statuses or 'missing'}"
                )
        return last_track_id

    async def query(
        self,
        question: str,
        *,
        mode: QueryMode = "mix",
        top_k: int = 12,
        chunk_top_k: int = 20,
    ) -> QueryResult:
        if not self._initialized:
            raise RuntimeError("LightRAG 尚未初始化")
        if mode not in SUPPORTED_QUERY_MODES:
            raise ValueError(f"不支持的查询模式: {mode}")
        normalization_started = time.perf_counter()
        normalization = (
            normalize_query(question)
            if self.settings.query_normalization_enabled
            else NormalizationResult(
                original_query=question,
                normalized_query=question.strip(),
                detected_model=None,
                detected_component=None,
                detected_parameter=None,
                added_aliases=(),
            )
        )
        normalized_question = normalization.normalized_query
        normalization_ms = (time.perf_counter() - normalization_started) * 1000
        if not normalized_question:
            raise ValueError("问题不能为空")
        options = QueryOptions(mode=mode, top_k=top_k, chunk_top_k=chunk_top_k)
        retrieval_started = time.perf_counter()
        evidence = await self._backend.aquery_data(normalized_question, options)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        retrieved = _extract_retrieved(evidence)
        retrieval_chunk_ids = tuple(item["chunk_id"] for item in retrieved)
        retrieval_meta = tuple(
            (item["file"], item["page"], item["chunk_id"]) for item in retrieved
        )
        selection_started = time.perf_counter()
        decision = select_evidence(
            normalized_question,
            evidence,
            diversify=self.settings.evidence_selection_diversity_enabled,
        )
        if not decision.allowed and self.settings.answer_grounding_enabled:
            partial_decision = select_partial_evidence(normalized_question, evidence)
            if not partial_decision.allowed:
                partial_decision = select_partial_evidence(
                    normalized_question, evidence, minimum_overlap=0
                )
            if partial_decision.allowed:
                decision = partial_decision
        evidence_selection_ms = (time.perf_counter() - selection_started) * 1000
        completed_context: tuple[ContextRecord, ...] = ()
        completion_candidates: list[dict[str, object]] = []
        completed_evidence: list[dict[str, object]] = []
        accepted_completion: list[dict[str, object]] = []
        coverage_requirements: tuple[str, ...] = ()
        coverage_before: tuple[str, ...] = ()
        coverage_after: tuple[str, ...] = ()
        completion_drop_reasons: list[str] = []
        completion_triggered = False
        supplemental_result = None
        supplemental_raw: list[dict[str, Any]] = []
        supplemental_grounding: tuple[EvidenceCandidate, ...] = ()
        if decision.allowed and self.settings.evidence_completion_enabled:
            registry = _load_context_registry(self.settings)
            selected_context_records = [
                registry[item.citation.chunk_id]
                for item in decision.selected
                if item.citation.chunk_id in registry
            ]
            plan = plan_conditional_completion(
                classify_question_type(normalized_question),
                selected_context_records,
                registry,
                is_negative=any(term in normalized_question for term in ("不存在", "没有", "无此", "是否存在")),
                max_completion=self.settings.evidence_completion_max,
            )
            coverage_requirements = plan.coverage_requirements
            coverage_before = plan.before
            coverage_after = plan.after
            completion_triggered = bool(plan.accepted)
            completed_context = tuple(item.record for item in plan.accepted)
            completion_drop_reasons.extend(item.reason for item in plan.rejected)
            for item in completed_context:
                source_type = next((candidate.relation for candidate in plan.accepted if candidate.chunk_id == item.chunk_id), "adjacent")
                source_type = "parent_context" if source_type == "parent" else "adjacent"
                relation = next(("previous" if record.previous_chunk_id == item.chunk_id else "next" for record in selected_context_records if record.previous_chunk_id == item.chunk_id or record.next_chunk_id == item.chunk_id), None)
                row = {
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "document_name": item.document_name,
                    "page_number": item.page_start,
                    "generation_id": item.generation_id,
                    "source_type": source_type,
                    "context_role": "context_only",
                    "completion_reason": next((candidate.reason for candidate in plan.accepted if candidate.chunk_id == item.chunk_id), "coverage_gap"),
                    "adjacent_relation": relation,
                    "used_for_answer": True,
                    "cited_in_answer": False,
                }
                completed_evidence.append(row)
                accepted_completion.append(row)
            completion_candidates = [
                {"chunk_id": item.chunk_id, "relation": item.relation, "reason": item.reason}
                for item in plan.candidates
            ]
            # One bounded second retrieval is allowed only after the
            # deterministic parent/adjacent plan leaves a gap.  Its results
            # are kept separate from initial ranking and metrics.
            pre_supplemental = run_supplemental_retrieval(
                normalized_question,
                knowledge_base_id=str(self.settings.qdrant_kb_id or ""),
                generation_id=str(self.settings.qdrant_generation or ""),
                selected=[{"chunk_id": item.citation.chunk_id} for item in decision.selected],
                coverage_before=coverage_before,
                coverage_after_context=coverage_after,
                question_type=classify_question_type(normalized_question),
                status="partial_answer",
                is_negative=any(term in normalized_question for term in ("不存在", "没有", "无此", "是否存在")),
                # Planning call: the actual bounded retrieval is performed
                # below with the exact SupplementalQuery.question.  Keeping
                # this side-effect-free avoids changing initial ranking.
                retrieve=lambda _query: (),
            ) if self.settings.supplemental_retrieval_enabled else None
            if pre_supplemental is not None and pre_supplemental.triggered and pre_supplemental.supplemental_query is not None:
                supplemental_query = pre_supplemental.supplemental_query
                supplemental_payload = await self._backend.aquery_data(
                    supplemental_query.question,
                    QueryOptions(mode=options.mode, top_k=supplemental_query.top_k, chunk_top_k=supplemental_query.top_k),
                )
                supplemental_raw = _extract_retrieved(supplemental_payload)
                supplemental_raw = [
                    {
                        **item,
                        "knowledge_base_id": str(self.settings.qdrant_kb_id or ""),
                        "generation_id": str(self.settings.qdrant_generation or ""),
                        "document_id": next((record.document_id for record in registry.values() if record.document_name == item["file"]), None),
                    }
                    for item in supplemental_raw
                ]
                supplemental_result = run_supplemental_retrieval(
                    supplemental_query.question,
                    knowledge_base_id=str(self.settings.qdrant_kb_id or ""),
                    generation_id=str(self.settings.qdrant_generation or ""),
                    selected=[{"chunk_id": item.citation.chunk_id} for item in decision.selected],
                    coverage_before=coverage_before,
                    coverage_after_context=coverage_after,
                    question_type=classify_question_type(normalized_question),
                    status="partial_answer",
                    is_negative=any(term in normalized_question for term in ("不存在", "没有", "无此", "是否存在")),
                    retrieve=lambda _query: supplemental_raw,
                )
                supplemental_grounding = tuple(
                    EvidenceCandidate(Citation(item["file"], item["page"], item["chunk_id"]), item["content"], len(decision.selected) + len(completed_context) + index)
                    for index, item in enumerate(supplemental_result.accepted, 1)
                )
                supplemental_evidence = [
                    {
                        "chunk_id": item.citation.chunk_id,
                        "document_name": item.citation.source_file,
                        "page_number": item.citation.page_number,
                        "generation_id": str(self.settings.qdrant_generation or ""),
                        "source_type": "supplemental",
                        "context_role": "supporting",
                        "completion_reason": "coverage_gap",
                        "used_for_answer": True,
                        "cited_in_answer": False,
                    }
                    for item in supplemental_grounding
                ]
                completed_evidence.extend(supplemental_evidence)
        if not decision.allowed:
            audit = (
                build_non_generation_audit(
                    generation_invoked=False,
                    output_status="insufficient_evidence",
                    failure_categories=("evidence_gate_refusal",),
                )
                if self.settings.grounding_audit_enabled
                else None
            )
            trace = _build_retrieval_trace(
                original_query=question,
                normalized_query=normalized_question,
                options=options,
                retrieved=retrieved,
                selected=(),
                cited=(),
                normalization_ms=normalization_ms,
                retrieval_ms=retrieval_ms,
                evidence_selection_ms=evidence_selection_ms,
                feature_flags=_phase10b3i_trace_flags(self.settings),
                normalization=normalization,
                grounding_audit=audit,
                completion_candidates=completion_candidates,
                completed_evidence=completed_evidence,
                provider_contexts=(),
                completion_applied=bool(completed_context),
                coverage_requirements=coverage_requirements,
                coverage_before=coverage_before,
                coverage_after=coverage_after,
                completion_triggered=completion_triggered,
                accepted_completion=accepted_completion,
                completion_drop_reasons=completion_drop_reasons,
            )
            return QueryResult(
                INSUFFICIENT_EVIDENCE_MESSAGE,
                (),
                mode,
                retrieval_chunk_ids,
                retrieval_meta,
                trace,
            )
        completion_candidates_for_grounding = tuple(
            EvidenceCandidate(
                Citation(item.document_name, item.page_start, item.chunk_id),
                item.text,
                len(decision.selected) + index,
            )
            for index, item in enumerate(completed_context, 1)
        )
        grounding_candidates = tuple(decision.selected) + completion_candidates_for_grounding + supplemental_grounding
        context = _selected_context(decision.selected)
        if completed_context:
            context += "\n\n" + "\n\n".join(
                f"[补充上下文：{item.document_name} 第{item.page_start}页]\n{item.text}"
                for item in completed_context
            )
        if supplemental_grounding:
            context += "\n\n" + "\n\n".join(
                f"[补充检索证据：{item.citation.source_file} 第{item.citation.page_number}页]\n{item.text}"
                for item in supplemental_grounding
            )
        # Freeze the exact provider boundary inputs for the admin-only lineage
        # trace.  These are derived from the same context sent below; no
        # additional retrieval or model call is performed.
        provider_evidence_ids = tuple(f"E{index}" for index in range(1, len(grounding_candidates) + 1))
        provider_primary_evidence_ids = tuple(f"E{index}" for index in range(1, len(decision.selected) + 1))
        provider_completed_evidence_ids = tuple(
            f"E{len(decision.selected) + index}" for index in range(1, len(completed_context) + 1)
        )
        provider_supplemental_evidence_ids = tuple(
            f"E{len(decision.selected) + len(completed_context) + index}"
            for index in range(1, len(supplemental_grounding) + 1)
        )
        provider_context_order = tuple(item.citation.chunk_id for item in grounding_candidates)
        provider_context_sha256 = hashlib.sha256(context.encode("utf-8")).hexdigest()
        provider_context_token_estimate = max(1, len(context) // 4) if context else 0
        selected_coverage = tuple(coverage_after)
        structured_enabled = self.settings.structured_citation_output_enabled
        source_registry = SourceRegistry.from_evidence(
            tuple(
                EvidenceRef(
                    evidence_id=f"E{index}",
                    chunk_id=item.citation.chunk_id,
                    generation_id=str(self.settings.qdrant_generation or ""),
                    document_name=item.citation.source_file,
                    citation_id=f"cite_{index}",
                    context_role="primary",
                    text=item.text,
                    is_child=True,
                )
                for index, item in enumerate(decision.selected, 1)
            )
        )
        requirement_registry = RequirementRegistry.from_requirements(coverage_requirements)
        structured_decision: StructuredCitationDecision | None = None
        structured_forced_status: Literal["insufficient_evidence"] | None = None
        backend_generate_call_count = 1
        if structured_enabled:
            system_prompt = _structured_citation_system_prompt(
                source_registry, requirement_registry
            )
            answer = (
                await self._backend.generate(
                    normalized_question,
                    context,
                    system_prompt,
                    response_format={"type": "json_object"},
                )
            ).strip()
            structured_decision = validate_structured_citation_output(
                answer,
                source_registry,
                requirement_registry,
                str(self.settings.qdrant_generation or ""),
            )
        else:
            system_prompt = _generation_system_prompt(context)
            if self.settings.answer_grounding_enabled:
                system_prompt += (
                    "答案可靠性要求：将每个可验证答案点绑定到证据中的具体内容；"
                    "未覆盖内容必须明确说明，不得补充常识或推断。\n"
                )
            answer = (
                await self._backend.generate(normalized_question, context, system_prompt)
            ).strip()
        if not answer:
            audit = (
                build_non_generation_audit(
                    answer="",
                    generation_invoked=True,
                    output_status="insufficient_evidence",
                    failure_categories=("generation_empty",),
                )
                if self.settings.grounding_audit_enabled
                else None
            )
            trace = _build_retrieval_trace(
                original_query=question,
                normalized_query=normalized_question,
                options=options,
                retrieved=retrieved,
                selected=decision.selected,
                cited=(),
                normalization_ms=normalization_ms,
                retrieval_ms=retrieval_ms,
                evidence_selection_ms=evidence_selection_ms,
                feature_flags=_phase10b3i_trace_flags(self.settings),
                normalization=normalization,
                grounding_audit=audit,
                completion_candidates=completion_candidates,
                completed_evidence=completed_evidence,
                provider_contexts=(context,) if context else (),
                completion_applied=bool(completed_context),
            )
            return QueryResult(
                INSUFFICIENT_EVIDENCE_MESSAGE,
                (),
                mode,
                retrieval_chunk_ids,
                retrieval_meta,
                trace,
            )
        citations = tuple(item.citation for item in decision.selected)
        if structured_decision is not None and structured_decision.valid:
            citations_by_chunk = {
                item.citation.chunk_id: item.citation for item in decision.selected
            }
            public_numbers = render_public_citation_numbers(
                structured_decision.answer_points
            )
            rendered_points: list[AnswerPoint] = []
            rendered_citations: list[Citation] = []
            for index, (point, numbers) in enumerate(
                zip(structured_decision.answer_points, public_numbers, strict=True), 1
            ):
                evidence_ids = tuple(
                    source_registry.resolve(source_id).evidence_id
                    for source_id in point.source_ids
                    if source_registry.resolve(source_id) is not None
                )
                for source_id in point.source_ids:
                    source = source_registry.resolve(source_id)
                    if source is None:
                        continue
                    citation = citations_by_chunk[source.chunk_id]
                    if citation not in rendered_citations:
                        rendered_citations.append(citation)
                rendered_points.append(
                    AnswerPoint(
                        point_id=f"P{index}",
                        content=(
                            point.text
                            + ""
                            .join(f"[{number}]" for number in numbers)
                        ),
                        evidence_ids=evidence_ids,
                        support_status="supported",
                    )
                )
            answer = "\n".join(point.content for point in rendered_points)
            citations = tuple(rendered_citations)
            grounded = GroundedAnswer(
                answer=answer,
                citations=citations,
                answer_points=tuple(rendered_points),
                status=structured_decision.status,
            )
        elif (
            structured_decision is not None
            and structured_decision.fallback_mode == "fallback_to_j0_postprocessing"
        ):
            answer = "\n".join(
                point.text for point in structured_decision.answer_points
            )
            grounded = (
                build_answer_plan(
                    answer,
                    grounding_candidates,
                    citations
                    + tuple(
                        item.citation for item in completion_candidates_for_grounding
                    ),
                )
                if self.settings.answer_grounding_enabled
                else None
            )
        elif structured_decision is not None:
            answer = INSUFFICIENT_EVIDENCE_MESSAGE
            citations = ()
            grounded = None
            structured_forced_status = "insufficient_evidence"
        else:
            grounded = (
                build_answer_plan(
                    answer,
                    grounding_candidates,
                    citations
                    + tuple(
                        item.citation for item in completion_candidates_for_grounding
                    ),
                )
                if self.settings.answer_grounding_enabled
                else None
            )
        support_reason_codes: list[str] = []
        generated_point_ids: list[str] = []
        rejected_point_ids: list[str] = []
        recovery = evaluate_post_retrieval_recovery(
            question_type=classify_question_type(normalized_question),
            selected=[
                {"chunk_id": item.citation.chunk_id, "generation_id": str(self.settings.qdrant_generation or ""),
                 "document_name": item.citation.source_file, "page_number": item.citation.page_number,
                 "text": item.text}
                for item in grounding_candidates
            ],
            available_candidates=[
                {"chunk_id": item["chunk_id"], "generation_id": str(self.settings.qdrant_generation or ""),
                 "document_name": item["file"], "page_number": item["page"], "text": item.get("content", "")}
                for item in retrieved
            ],
            coverage_requirements=coverage_requirements,
            provider_evidence_ids=provider_evidence_ids,
            generated_answer_point_ids=tuple(point.point_id for point in (grounded.answer_points if grounded else ())),
            grounding_removed_point_ids=tuple(rejected_point_ids),
            generation_status=grounded.status if grounded else "success",
        )
        if (
            grounded is not None
            and self.settings.support_validator_v2_enabled
            and not (structured_decision is not None and structured_decision.valid)
        ):
            evidence_registry = {
                f"E{index}": EvidenceRef(
                    evidence_id=f"E{index}",
                    chunk_id=item.citation.chunk_id,
                    generation_id=str(self.settings.qdrant_generation or ""),
                    document_name=item.citation.source_file,
                    citation_id=f"cite_{index}",
                    context_role="primary",
                    text=item.text,
                    is_child=True,
                )
                for index, item in enumerate(grounding_candidates, 1)
            }
            structured_points = tuple(
                StructuredAnswerPoint(point.point_id, point.content, point.evidence_ids)
                for point in grounded.answer_points
            )
            validation = validate_answer_points(
                structured_points,
                evidence_registry,
                generation_id=str(self.settings.qdrant_generation or ""),
                safety_question=classify_question_type(normalized_question) == "safety",
            )
            generated_point_ids = [point.point_id for point in grounded.answer_points]
            rejected_point_ids = list(validation.invalid_point_ids)
            for result in validation.point_results:
                if result.reason:
                    support_reason_codes.extend(result.reason.split(";"))
            if validation.points:
                retained_ids = {point.point_id for point in validation.points}
                retained_answer_points = tuple(
                    replace(point, support_status="supported")
                    for point in grounded.answer_points
                    if point.point_id in retained_ids
                )
                retained_evidence = {
                    evidence_id
                    for point in validation.points
                    for evidence_id in point.evidence_ids
                }
                retained_citations = tuple(
                    citation
                    for index, citation in enumerate(grounded.citations, 1)
                    if f"E{index}" in retained_evidence
                )
                grounded = replace(
                    grounded,
                    answer="\n".join(point.content for point in retained_answer_points),
                    citations=retained_citations,
                    answer_points=retained_answer_points,
                    status=validation.status,
                    failure_categories=tuple(dict.fromkeys((*grounded.failure_categories, *support_reason_codes))),
                )
            elif grounded.answer_points:
                grounded = replace(
                    grounded,
                    answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                    citations=(),
                    answer_points=(),
                    status="insufficient_evidence",
                    failure_categories=tuple(dict.fromkeys((*grounded.failure_categories, "support_validation_rejected_all"))),
                )
        trace = _build_retrieval_trace(
            original_query=question,
            normalized_query=normalized_question,
            options=options,
            retrieved=retrieved,
            selected=grounding_candidates,
            cited=grounded.citations if grounded else citations,
            normalization_ms=normalization_ms,
            retrieval_ms=retrieval_ms,
            evidence_selection_ms=evidence_selection_ms,
            feature_flags=_phase10b3i_trace_flags(self.settings),
            normalization=normalization,
            answer_plan=grounded.answer_points if grounded else (),
            grounding_audit=(grounded.grounding_audit if grounded and self.settings.grounding_audit_enabled else None),
            completion_candidates=completion_candidates,
            completed_evidence=completed_evidence,
            completion_applied=bool(completed_context),
            coverage_requirements=coverage_requirements,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            completion_triggered=completion_triggered,
            accepted_completion=accepted_completion,
            completion_context_order=tuple(item.chunk_id for item in completed_context),
            completion_sent_to_provider=bool(completed_context),
            completion_bound_answer_points=tuple(
                point.point_id for point in (grounded.answer_points if grounded else ())
                if any(evidence_id.startswith("E") and int(evidence_id[1:]) > len(decision.selected) for evidence_id in point.evidence_ids)
            ),
            completion_bound_claims=tuple(
                point.point_id for point in (grounded.answer_points if grounded else ())
                if any(evidence_id.startswith("E") and int(evidence_id[1:]) > len(decision.selected) for evidence_id in point.evidence_ids)
            ),
            completion_drop_reasons=completion_drop_reasons,
            coverage_requirement_ids=coverage_requirements,
            coverage_funnel_stage=("supplemental" if supplemental_result and supplemental_result.triggered else ("completion" if completed_context else "initial")),
            supplemental_retrieval_triggered=bool(supplemental_result and supplemental_result.triggered),
            supplemental_query_text=(supplemental_result.supplemental_query.question if supplemental_result and supplemental_result.supplemental_query else None),
            supplemental_query_sha256=(hash_supplemental_query(supplemental_result.supplemental_query.question) if supplemental_result and supplemental_result.supplemental_query else None),
            supplemental_query_different_from_normalized=bool(
                supplemental_result
                and supplemental_result.supplemental_query
                and supplemental_result.supplemental_query.question != normalized_question
            ),
            supplemental_candidates=(tuple({"chunk_id": item.get("chunk_id"), "rank": item.get("rank"), "document_id": item.get("document_id"), "generation_id": item.get("generation_id")} for item in supplemental_result.retrieved) if supplemental_result else ()),
            supplemental_accepted=(tuple({"chunk_id": item.get("chunk_id"), "rank": item.get("rank"), "document_id": item.get("document_id"), "generation_id": item.get("generation_id")} for item in supplemental_result.accepted) if supplemental_result else ()),
            supplemental_rejected=(tuple({"chunk_id": item.get("chunk_id"), "rank": item.get("rank"), "document_id": item.get("document_id"), "generation_id": item.get("generation_id")} for item in supplemental_result.rejected) if supplemental_result else ()),
            provider_evidence_ids=provider_evidence_ids,
            provider_primary_evidence_ids=provider_primary_evidence_ids,
            provider_completed_evidence_ids=provider_completed_evidence_ids,
            provider_supplemental_evidence_ids=provider_supplemental_evidence_ids,
            provider_context_order=provider_context_order,
            provider_contexts=(context,) if context else (),
            provider_context_sha256=provider_context_sha256,
            provider_evidence_count=len(grounding_candidates),
            provider_context_token_estimate=provider_context_token_estimate,
            backend_second_query_called=bool(supplemental_result and supplemental_result.triggered),
            structured_citation_flag=structured_enabled,
            json_mode_enabled=structured_enabled,
            source_registry_count=len(source_registry.entries),
            source_registry_sha256=source_registry.sha256 if structured_enabled else None,
            requirement_registry_count=len(requirement_registry.entries),
            requirement_registry_sha256=(
                requirement_registry.sha256 if structured_enabled else None
            ),
            provider_raw_response_sha256=(
                structured_decision.raw_response_sha256
                if structured_decision is not None
                else None
            ),
            parsed_structured_output_sha256=(
                structured_decision.parsed_output_sha256
                if structured_decision is not None
                else None
            ),
            structured_output_valid=(
                structured_decision.valid if structured_decision is not None else False
            ),
            structured_citation_fallback=bool(
                structured_decision and structured_decision.fallback_mode
            ),
            structured_citation_fallback_mode=(
                structured_decision.fallback_mode
                if structured_decision is not None
                else None
            ),
            structured_citation_fallback_reason=(
                structured_decision.fallback_reason
                if structured_decision is not None
                else None
            ),
            backend_generate_call_count=backend_generate_call_count,
            coverage_after_parent_adjacent=coverage_after,
            selected_coverage=selected_coverage,
            generated_coverage=tuple(point.point_id for point in (grounded.answer_points if grounded else ())),
            grounding_retained_coverage=selected_coverage if grounded and grounded.answer_points else (),
            grounding_answer_point_identity=tuple(point.point_id for point in (grounded.answer_points if grounded else ())),
            grounding_retained_answer_points=tuple(point.point_id for point in (grounded.answer_points if grounded else ())),
            grounding_removed_answer_points=tuple(rejected_point_ids),
            grounding_false_negative_diagnostics=(recovery.to_dict(),),
            generated_answer_points=tuple(generated_point_ids),
            rejected_answer_points=tuple(rejected_point_ids),
            support_validation_reason_codes=tuple(dict.fromkeys(support_reason_codes)),
            final_answer_point_ids=tuple(point.point_id for point in (grounded.answer_points if grounded else ())),
            unresolved_requirement_ids=(
                structured_decision.unresolved_requirement_ids
                if structured_decision is not None
                else tuple(item for item in coverage_requirements if item not in coverage_after)
            ),
        )
        return QueryResult(
            grounded.answer if grounded else answer,
            grounded.citations if grounded else citations,
            mode,
            retrieval_chunk_ids,
            retrieval_meta,
            trace,
            (
                grounded.status
                if grounded
                else (structured_forced_status or "success")
            ),
            grounded.answer_points if grounded else (),
            grounded.failure_categories if grounded else (),
        )
