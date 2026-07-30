from __future__ import annotations

from pathlib import Path

import pytest
from industrial_rag.citation_formatter import Citation, encode_source_ref
from industrial_rag.config import (
    INDEX_METADATA_FILENAME,
    SUPPORTED_QUERY_MODES,
    Settings,
    StorageCompatibilityError,
    check_storage_compatibility,
)
from industrial_rag.document_parser import DocumentChunk
from industrial_rag.lightrag_service import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    LightRAGService,
    build_official_backend,
)


class FakeLightRAGBackend:
    def __init__(self, *, has_evidence: bool = True) -> None:
        self.has_evidence = has_evidence
        self.initialized = False
        self.closed = False
        self.insert_call: dict[str, object] | None = None
        self.insert_calls: list[dict[str, object]] = []
        self.query_modes: list[str] = []

    async def initialize_storages(self) -> None:
        self.initialized = True

    async def finalize_storages(self) -> None:
        self.closed = True

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        self.insert_call = {"input": input, **kwargs}
        self.insert_calls.append(self.insert_call)
        return f"track-test-{len(self.insert_calls)}"

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        return {track_id: "processed"}

    async def aquery_data(self, query: str, param: object) -> dict[str, object]:
        self.query_modes.append(param.mode)  # type: ignore[attr-defined]
        chunks = []
        references = []
        if self.has_evidence:
            source = encode_source_ref(Citation("pump.pdf", 7, "pump-p7-c1"))
            chunks = [{"content": "轴承温度过高时检查润滑。", "file_path": source}]
            references = [{"file_path": source}]
        return {
            "status": "success",
            "data": {
                "entities": [],
                "relationships": [],
                "chunks": chunks,
                "references": references,
            },
        }

    async def aquery(self, query: str, param: object, system_prompt: str) -> str:
        assert "手册" in system_prompt
        return "应检查轴承润滑状态。"


def _settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "LLM_MODEL": "kimi-k2.6",
            "EMBEDDING_MODEL": "text-embedding-v4",
            "EMBEDDING_DIM": "1024",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
        }
    )


def test_settings_lock_required_bailian_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert SUPPORTED_QUERY_MODES == ("mix", "hybrid", "local", "global", "naive")
    assert "bypass" not in SUPPORTED_QUERY_MODES
    assert settings.llm_model == "kimi-k2.6"
    assert settings.embedding_model == "text-embedding-v4"
    assert settings.embedding_dim == 1024
    assert settings.llm_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_storage_dimension_mismatch_requires_manual_rebuild(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / INDEX_METADATA_FILENAME).write_text(
        '{"embedding_model":"old-model","embedding_dim":1536}', encoding="utf-8"
    )

    with pytest.raises(StorageCompatibilityError, match="重建"):
        check_storage_compatibility(storage, "text-embedding-v4", 1024)


def test_official_backend_accepts_parser_chunks_and_locks_embedding_dimension(
    tmp_path: Path,
) -> None:
    backend = build_official_backend(_settings(tmp_path))
    rag = backend._rag  # type: ignore[attr-defined]

    assert rag.chunk_token_size == 1600
    assert rag.embedding_func.embedding_dim == 1024
    assert rag.embedding_func.send_dimensions is True


@pytest.mark.asyncio
async def test_fake_service_initializes_inserts_and_returns_metadata_citations(
    tmp_path: Path,
) -> None:
    backend = FakeLightRAGBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    chunk = DocumentChunk(
        chunk_id="pump-p7-c1",
        text="轴承温度过高时检查润滑。",
        source_file="pump.pdf",
        page_number=7,
        section_title="轴承故障",
    )

    await service.initialize()
    track_id = await service.ingest([chunk])
    result = await service.query("轴承温度过高怎么办？", mode="mix")
    await service.close()

    assert backend.initialized and backend.closed
    assert track_id == "track-test-1"
    assert backend.insert_call is not None
    assert backend.insert_call["ids"][0].startswith("manual-")  # type: ignore[index,union-attr]
    assert "pump-p7-c1" in backend.insert_call["input"][0]  # type: ignore[index]
    assert "第7页" in backend.insert_call["input"][0]  # type: ignore[index]
    assert result.answer == "应检查轴承润滑状态。"
    assert [item.display for item in result.citations] == ["[pump.pdf，第7页]"]


@pytest.mark.asyncio
async def test_ingest_serializes_manuals_and_preserves_page_chunk_boundaries(
    tmp_path: Path,
) -> None:
    backend = FakeLightRAGBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()
    chunks = [
        DocumentChunk("pump-p1-c1", "第一页", "pump.pdf", 1, "章节一"),
        DocumentChunk("pump-p2-c1", "第二页", "pump.pdf", 2, "章节二"),
        DocumentChunk("other-p1-c1", "另一手册", "other.pdf", 1, "章节"),
    ]

    track_id = await service.ingest(chunks)

    assert track_id == "track-test-2"
    assert len(backend.insert_calls) == 2
    first_call = backend.insert_calls[0]
    assert first_call["file_paths"] == ["pump.pdf"]
    assert "pump-p1-c1" in first_call["input"][0]  # type: ignore[index]
    assert "pump-p2-c1" in first_call["input"][0]  # type: ignore[index]
    assert first_call["split_by_character_only"] is True
    assert "INDUSTRIAL_RAG_CHUNK_BOUNDARY" in first_call["split_by_character"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_ingest_raises_when_lightrag_marks_a_manual_failed(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()

    async def failed_status(track_id: str) -> dict[str, str]:
        return {track_id: "failed"}

    backend.get_track_status = failed_status  # type: ignore[method-assign]
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()
    chunk = DocumentChunk("pump-p1-c1", "正文", "pump.pdf", 1, "章节")

    with pytest.raises(RuntimeError, match="导入失败"):
        await service.ingest([chunk])


@pytest.mark.asyncio
async def test_ingest_accepts_dup_status_from_lightrag(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()

    async def dup_status(track_id: str) -> dict[str, str]:
        return {"dup-ddoc123": "processed", track_id: "processed"}

    backend.get_track_status = dup_status  # type: ignore[method-assign]
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()
    chunk = DocumentChunk("pump-p1-c1", "正文", "pump.pdf", 1, "章节")

    track_id = await service.ingest([chunk])
    assert track_id == "track-test-1"


@pytest.mark.asyncio
async def test_fake_service_returns_fixed_message_without_evidence(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend(has_evidence=False)
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("手册没有的问题", mode="naive")

    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.citations == ()


@pytest.mark.asyncio
async def test_query_prompt_preserves_lightrag_retrieval_context(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()

    async def format_like_official_lightrag(query: str, param: object, system_prompt: str) -> str:
        rendered = system_prompt.format(
            response_type="Multiple Paragraphs",
            user_prompt="n/a",
            context_data="检索到的手册证据",
        )
        assert "检索到的手册证据" in rendered
        return "依据检索证据回答。"

    backend.aquery = format_like_official_lightrag  # type: ignore[method-assign]
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("离心泵启动前需要检查什么？", mode="mix")

    assert result.answer == "依据检索证据回答。"


@pytest.mark.asyncio
async def test_naive_query_prompt_preserves_lightrag_retrieval_context(
    tmp_path: Path,
) -> None:
    backend = FakeLightRAGBackend()

    async def format_like_official_naive_lightrag(
        query: str, param: object, system_prompt: str
    ) -> str:
        rendered = system_prompt.format(
            response_type="Multiple Paragraphs",
            user_prompt="n/a",
            content_data="朴素检索到的手册证据",
        )
        assert "朴素检索到的手册证据" in rendered
        return "依据朴素检索证据回答。"

    backend.aquery = format_like_official_naive_lightrag  # type: ignore[method-assign]
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("离心泵启动前需要检查什么？", mode="naive")

    assert result.answer == "依据朴素检索证据回答。"


@pytest.mark.asyncio
async def test_hybrid_query_passes_mode_and_uses_kg_system_prompt(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()
    captured: dict[str, object] = {}

    async def capture_hybrid_prompt(query: str, param: object, system_prompt: str) -> str:
        captured["mode"] = param.mode  # type: ignore[attr-defined]
        captured["system_prompt"] = system_prompt
        rendered = system_prompt.format(
            response_type="Multiple Paragraphs",
            user_prompt="n/a",
            context_data="hybrid 检索到的手册证据",
        )
        assert "hybrid 检索到的手册证据" in rendered
        assert "{content_data}" not in system_prompt
        return "依据 hybrid 检索证据回答。"

    backend.aquery = capture_hybrid_prompt  # type: ignore[method-assign]
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("轴承温度过高的原因和对应处理方法是什么？", mode="hybrid")

    assert result.mode == "hybrid"
    assert result.answer == "依据 hybrid 检索证据回答。"
    assert result.citations
    assert backend.query_modes == ["hybrid"]
    assert captured["mode"] == "hybrid"
    assert "{context_data}" in str(captured["system_prompt"])
    assert "{content_data}" not in str(captured["system_prompt"])


@pytest.mark.asyncio
async def test_all_five_supported_modes_are_accepted(tmp_path: Path) -> None:
    expected_modes = ("mix", "hybrid", "local", "global", "naive")
    assert expected_modes == SUPPORTED_QUERY_MODES
    assert "bypass" not in SUPPORTED_QUERY_MODES

    backend = FakeLightRAGBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    for mode in expected_modes:
        result = await service.query("离心泵启动前需要检查什么？", mode=mode)
        assert result.mode == mode

    assert backend.query_modes == list(expected_modes)


@pytest.mark.asyncio
async def test_service_rejects_modes_outside_the_scoped_five(tmp_path: Path) -> None:
    service = LightRAGService(_settings(tmp_path), backend=FakeLightRAGBackend())
    await service.initialize()

    with pytest.raises(ValueError, match="查询模式"):
        await service.query("测试问题", mode="bypass")  # type: ignore[arg-type]
