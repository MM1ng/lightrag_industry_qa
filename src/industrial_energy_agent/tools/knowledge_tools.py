"""Structured manual retrieval tool backed by the injected RAG adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, RootModel

from industrial_energy_agent.domain.models import ManualCitation, TraceEvent
from industrial_energy_agent.rag.base import (
    CitationChunkSource,
    CitationSource,
    RAGApplicationError,
    RAGCapabilityError,
    RAGRequestError,
    RAGResponseError,
    SearchResult,
    VerifiedSearchMode,
)
from industrial_energy_agent.tools.common import (
    SafeStructuredTool,
    ToolFailure,
    ToolInputModel,
    ToolModel,
    build_safe_structured_tool,
    dump_result,
    make_error,
    make_trace,
    new_request_id,
    started_at,
)

_CHUNK_HEADER = re.compile(r"\[chunk_id=(?P<chunk_id>[^;\]\s]+);page=(?P<page>[1-9][0-9]*)\]")


class KnowledgeRAGBoundary(Protocol):
    def search(
        self,
        query: str,
        *,
        mode: VerifiedSearchMode,
        top_k: int,
        local_filters: Mapping[str, str] | None = None,
    ) -> SearchResult: ...

    def get_sources(self, source_ids: Sequence[str]) -> list[CitationSource]: ...


class SearchManualKnowledgeInput(ToolInputModel):
    query: str = Field(min_length=3, max_length=2_000)
    top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5
    mode: VerifiedSearchMode = "hybrid"


class ManualKnowledgeItem(ToolModel):
    excerpt: str
    citation: ManualCitation


class SearchManualKnowledgeSuccess(ToolModel):
    ok: Literal[True] = True
    items: list[ManualKnowledgeItem]
    trace: TraceEvent


class SearchManualKnowledgeFailure(ToolFailure):
    pass


class SearchManualKnowledgeResult(
    RootModel[SearchManualKnowledgeSuccess | SearchManualKnowledgeFailure]
):
    pass


def _reference_ids(result: SearchResult) -> tuple[str, ...]:
    values: list[str] = []
    for reference in result.references:
        file_path = reference.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            basename = file_path.strip().replace("\\", "/").rsplit("/", 1)[-1]
            if basename:
                values.append(basename)
        reference_id = reference.get("reference_id")
        if isinstance(reference_id, str) and reference_id.strip():
            values.append(reference_id.strip())
    return tuple(dict.fromkeys(values))


def _source_for_chunk(
    chunk_id: str,
    sources: Sequence[CitationSource],
) -> CitationChunkSource | None:
    for source in sources:
        for chunk in source.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
    return None


def _items(
    result: SearchResult, sources: Sequence[CitationSource], top_k: int
) -> list[ManualKnowledgeItem]:
    items: list[ManualKnowledgeItem] = []
    seen_chunk_ids: set[str] = set()
    for chunk in result.chunks:
        content = chunk.get("content")
        if not isinstance(content, str):
            raise RAGResponseError("RAG chunk content is invalid")
        matches = tuple(_CHUNK_HEADER.finditer(content))
        if not matches:
            continue
        for index, match in enumerate(matches):
            chunk_id = match.group("chunk_id")
            source = _source_for_chunk(chunk_id, sources)
            if source is None:
                raise RAGResponseError("RAG chunk is not registered locally")
            if int(match.group("page")) != source.page_number:
                raise RAGResponseError("RAG chunk page conflicts with local metadata")
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            remote_text = content[match.end() : body_end].strip()
            local_text = source.text.strip()
            if not remote_text or not local_text.startswith(remote_text):
                raise RAGResponseError("RAG chunk body conflicts with local metadata")
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            excerpt = local_text[:2_000]
            citation = ManualCitation(
                citation_id=chunk_id,
                source_file=source.source_file,
                document_title=source.document_title,
                page_number=source.page_number,
                section_title=source.section_title,
                chunk_id=chunk_id,
                excerpt=excerpt,
            )
            items.append(ManualKnowledgeItem(excerpt=excerpt, citation=citation))
    return items[:top_k]


class SearchManualKnowledgeService:
    def __init__(self, rag: KnowledgeRAGBoundary) -> None:
        self._rag = rag

    def execute(self, args: SearchManualKnowledgeInput) -> SearchManualKnowledgeResult:
        started = started_at()
        if len(args.query.strip()) < 3 or not 1 <= args.top_k <= 20:
            return self._failure(
                args,
                started,
                code="INVALID_INPUT",
                message="检索词至少需要三个字符, top_k 必须在 1 到 20 之间。",
            )
        try:
            result = self._rag.search(
                args.query,
                mode=args.mode,
                top_k=args.top_k,
                local_filters=None,
            )
            references = _reference_ids(result)
            sources = self._rag.get_sources(references) if references else []
            items = _items(result, sources, args.top_k)
        except RAGRequestError as error:
            return self._failure(
                args,
                started,
                code="RAG_DEPENDENCY_ERROR",
                message="手册检索服务暂时不可用。",
                retryable=error.retryable,
            )
        except (RAGResponseError, RAGApplicationError, RAGCapabilityError):
            return self._failure(
                args,
                started,
                code="RAG_RESPONSE_ERROR",
                message="手册检索结果缺少可验证的物理页引用。",
            )
        except Exception:
            return self._failure(
                args,
                started,
                code="RAG_DEPENDENCY_ERROR",
                message="手册检索服务暂时不可用。",
                retryable=True,
            )
        if (result.chunks or result.references) and not items:
            return self._failure(
                args,
                started,
                code="RAG_RESPONSE_ERROR",
                message="手册检索结果缺少可验证的物理页引用。",
            )
        success = SearchManualKnowledgeSuccess(
            items=items,
            trace=make_trace(
                request_id=args.request_id,
                tool="search_manual_knowledge",
                started=started,
                status="success",
                evidence_count=len(items),
                parameter_summary={
                    "query_length": len(args.query),
                    "top_k": args.top_k,
                    "mode": args.mode,
                },
            ),
        )
        return SearchManualKnowledgeResult(root=success)

    @staticmethod
    def _failure(
        args: SearchManualKnowledgeInput,
        started: float,
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> SearchManualKnowledgeResult:
        failure = SearchManualKnowledgeFailure(
            error=make_error(code, message, retryable=retryable),
            trace=make_trace(
                request_id=args.request_id,
                tool="search_manual_knowledge",
                started=started,
                status="failure",
                parameter_summary={
                    "query_length": len(args.query),
                    "top_k": args.top_k,
                    "mode": args.mode,
                },
                error_code=code,
            ),
        )
        return SearchManualKnowledgeResult(root=failure)


def build_search_manual_knowledge_tool(rag: KnowledgeRAGBoundary) -> SafeStructuredTool:
    service = SearchManualKnowledgeService(rag)

    def search_manual_knowledge(
        query: str,
        top_k: int = 5,
        mode: VerifiedSearchMode = "hybrid",
        request_id: str = "",
    ) -> dict[str, Any]:
        args = SearchManualKnowledgeInput(
            query=query,
            top_k=top_k,
            mode=mode,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=search_manual_knowledge,
        name="search_manual_knowledge",
        description="检索已登记设备手册并返回物理页引用, 不生成第二个答案。",
        args_schema=SearchManualKnowledgeInput,
    )
