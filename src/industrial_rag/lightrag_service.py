"""Small service around the verified official LightRAG 1.5.4 async API."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol, cast

from industrial_rag.citation_formatter import Citation, collect_citations, encode_chunk_header
from industrial_rag.config import (
    SUPPORTED_QUERY_MODES,
    Settings,
    check_storage_compatibility,
    write_storage_metadata,
)
from industrial_rag.document_parser import DocumentChunk
from industrial_rag.evidence_policy import EvidenceCandidate, _tokens, select_evidence
from industrial_rag.retrieval_trace import (
    TRACE_VERSION,
    RetrievalExecutionTrace,
    RetrievalTraceItem,
    SelectedEvidenceTrace,
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


class LightRAGBackend(Protocol):
    async def initialize_storages(self) -> None: ...

    async def finalize_storages(self) -> None: ...

    async def ainsert(self, input: list[str], **kwargs: object) -> str: ...

    async def get_track_status(self, track_id: str) -> dict[str, str]: ...

    async def aquery_data(self, query: str, param: QueryOptions) -> dict[str, object]: ...

    async def generate(self, question: str, context: str, system_prompt: str) -> str: ...


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

    async def generate(self, question: str, context: str, system_prompt: str) -> str:
        result = await self._llm_model_func(question, system_prompt=system_prompt)
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
        trace_version=TRACE_VERSION,
        original_query=original_query,
        normalized_query=normalized_query,
        retrieval_config=(
            ("mode", options.mode),
            ("top_k", options.top_k),
            ("chunk_top_k", options.chunk_top_k),
            ("rerank_enabled", options.enable_rerank),
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
    )


def _generation_system_prompt(context: str) -> str:
    return _SYSTEM_PROMPT_BASE + _SELECTED_CONTEXT_LABEL + context


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

    async def query(self, question: str, *, mode: QueryMode = "mix") -> QueryResult:
        if not self._initialized:
            raise RuntimeError("LightRAG 尚未初始化")
        if mode not in SUPPORTED_QUERY_MODES:
            raise ValueError(f"不支持的查询模式: {mode}")
        normalization_started = time.perf_counter()
        normalized_question = question.strip()
        normalization_ms = (time.perf_counter() - normalization_started) * 1000
        if not normalized_question:
            raise ValueError("问题不能为空")
        options = QueryOptions(mode=mode)
        retrieval_started = time.perf_counter()
        evidence = await self._backend.aquery_data(normalized_question, options)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        retrieved = _extract_retrieved(evidence)
        retrieval_chunk_ids = tuple(item["chunk_id"] for item in retrieved)
        retrieval_meta = tuple(
            (item["file"], item["page"], item["chunk_id"]) for item in retrieved
        )
        selection_started = time.perf_counter()
        decision = select_evidence(normalized_question, evidence)
        evidence_selection_ms = (time.perf_counter() - selection_started) * 1000
        if not decision.allowed:
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
            )
            return QueryResult(
                INSUFFICIENT_EVIDENCE_MESSAGE,
                (),
                mode,
                retrieval_chunk_ids,
                retrieval_meta,
                trace,
            )
        context = _selected_context(decision.selected)
        system_prompt = _generation_system_prompt(context)
        answer = (await self._backend.generate(normalized_question, context, system_prompt)).strip()
        if not answer:
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
        trace = _build_retrieval_trace(
            original_query=question,
            normalized_query=normalized_question,
            options=options,
            retrieved=retrieved,
            selected=decision.selected,
            cited=citations,
            normalization_ms=normalization_ms,
            retrieval_ms=retrieval_ms,
            evidence_selection_ms=evidence_selection_ms,
        )
        return QueryResult(
            answer,
            citations,
            mode,
            retrieval_chunk_ids,
            retrieval_meta,
            trace,
        )
