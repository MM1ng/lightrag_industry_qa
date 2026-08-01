"""Small service around the verified official LightRAG 1.5.4 async API."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol, cast

from industrial_rag.citation_formatter import Citation, encode_chunk_header
from industrial_rag.config import (
    SUPPORTED_QUERY_MODES,
    Settings,
    check_storage_compatibility,
    write_storage_metadata,
)
from industrial_rag.document_parser import DocumentChunk
from industrial_rag.evidence_policy import EvidenceCandidate, select_evidence
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


def build_official_backend(settings: Settings) -> LightRAGBackend:
    """Build against the locally installed HKUDS LightRAG API, with explicit 1024 dimensions."""

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

    active_model_index = 0

    async def llm_model_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        nonlocal active_model_index
        kwargs.pop("model", None)
        for model_index in range(active_model_index, len(settings.llm_models)):
            model = settings.llm_models[model_index]
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
                    or model_index == len(settings.llm_models) - 1
                ):
                    raise
                logger.warning(
                    "DashScope model %s unavailable; trying configured fallback model.", model
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
        if not question.strip():
            raise ValueError("问题不能为空")
        options = QueryOptions(mode=mode)
        evidence = await self._backend.aquery_data(question.strip(), options)
        decision = select_evidence(question.strip(), evidence)
        if not decision.allowed:
            return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), mode)
        context = _selected_context(decision.selected)
        system_prompt = _generation_system_prompt(context)
        answer = (await self._backend.generate(question.strip(), context, system_prompt)).strip()
        if not answer:
            return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), mode)
        return QueryResult(answer, tuple(item.citation for item in decision.selected), mode)
