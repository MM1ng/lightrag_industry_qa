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
    _is_model_failover_error,
    build_official_backend,
)


class FakeLightRAGBackend:
    def __init__(
        self, *, has_evidence: bool = True, evidence_payload: dict[str, object] | None = None
    ) -> None:
        self.has_evidence = has_evidence
        self.evidence_payload = evidence_payload
        self.initialized = False
        self.closed = False
        self.insert_call: dict[str, object] | None = None
        self.insert_calls: list[dict[str, object]] = []
        self.query_modes: list[str] = []
        self.generate_calls: list[tuple[str, str, str]] = []

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
        if self.evidence_payload is not None:
            return self.evidence_payload
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

    async def generate(self, question: str, context: str, system_prompt: str) -> str:
        self.generate_calls.append((question, context, system_prompt))
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
async def test_official_backend_falls_back_after_quota_error_and_keeps_active_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightrag.llm import openai as lightrag_openai

    attempted_models: list[str] = []

    async def complete(**kwargs: object) -> str:
        model = kwargs["model"]
        assert isinstance(model, str)
        attempted_models.append(model)
        if model == "primary-model":
            raise RuntimeError("free quota exhausted")
        return f"answer from {model}"

    monkeypatch.setattr(lightrag_openai, "openai_complete_if_cache", complete)
    settings = Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_MODEL": "primary-model",
            "LLM_FALLBACK_MODELS": "fallback-one,fallback-two",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
        }
    )
    backend = build_official_backend(settings)

    assert await backend.generate("question one", "", "system") == "answer from fallback-one"
    assert await backend.generate("question two", "", "system") == "answer from fallback-one"
    assert attempted_models == ["primary-model", "fallback-one", "fallback-one"]


@pytest.mark.asyncio
async def test_official_backend_does_not_fail_over_on_ordinary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightrag.llm import openai as lightrag_openai

    attempted_models: list[str] = []

    async def complete(**kwargs: object) -> str:
        model = kwargs["model"]
        assert isinstance(model, str)
        attempted_models.append(model)
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(lightrag_openai, "openai_complete_if_cache", complete)
    settings = Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_MODEL": "primary-model",
            "LLM_FALLBACK_MODELS": "fallback-one",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
        }
    )
    backend = build_official_backend(settings)

    with pytest.raises(RuntimeError, match="connection reset"):
        await backend.generate("question", "", "system")
    assert attempted_models == ["primary-model"]


@pytest.mark.asyncio
async def test_official_backend_raises_after_all_configured_models_are_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightrag.llm import openai as lightrag_openai

    attempted_models: list[str] = []

    async def complete(**kwargs: object) -> str:
        model = kwargs["model"]
        assert isinstance(model, str)
        attempted_models.append(model)
        raise RuntimeError("rate limit reached")

    monkeypatch.setattr(lightrag_openai, "openai_complete_if_cache", complete)
    settings = Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_MODEL": "primary-model",
            "LLM_FALLBACK_MODELS": "fallback-one",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
        }
    )
    backend = build_official_backend(settings)

    with pytest.raises(RuntimeError, match="rate limit"):
        await backend.generate("question", "", "system")
    assert attempted_models == ["primary-model", "fallback-one"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("free quota exhausted"), True),
        (RuntimeError("rate limit reached"), True),
        (RuntimeError("model unavailable"), True),
        (RuntimeError("connection reset by peer"), False),
        (RuntimeError("invalid request payload"), False),
    ],
)
def test_model_failover_error_classification(error: Exception, expected: bool) -> None:
    assert _is_model_failover_error(error) is expected


def test_model_failover_error_classifies_http_429() -> None:
    class RateLimitedError(RuntimeError):
        status_code = 429

    assert _is_model_failover_error(RateLimitedError("request rejected"))


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
async def test_query_refuses_before_generation_when_policy_rejects(tmp_path: Path) -> None:
    evidence = _payload(
        [
            ("pump.pdf", 7, "pump-p7-c1", "轴承润滑应定期检查。"),
        ]
    )
    backend = FakeLightRAGBackend(evidence_payload=evidence)
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("火星基地零重力维护周期？")

    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.citations == ()
    assert backend.generate_calls == []


@pytest.mark.asyncio
async def test_query_generates_from_selected_chunks_and_returns_three_citations(
    tmp_path: Path,
) -> None:
    evidence = _payload(
        [
            ("2196-ANSI-Manual-Chinese.pdf", 1, "sumit-c1", "SUMMIT 2196 入口管路应短直布置。"),
            ("2196-ANSI-Manual-Chinese.pdf", 2, "sumit-c2", "SUMMIT 2196 入口管路避免空气袋。"),
            ("2196-ANSI-Manual-Chinese.pdf", 3, "sumit-c3", "SUMMIT 2196 入口管路要减少弯头。"),
            ("2196-ANSI-Manual-Chinese.pdf", 4, "sumit-c4", "SUMMIT 2196 入口管路保持密封。"),
            ("t1739cn.pdf", 5, "desmi-c5", "DESMI 泵的入口管路安装说明。"),
        ]
    )
    backend = FakeLightRAGBackend(evidence_payload=evidence)
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("SUMMIT 2196 入口管路如何布置？")

    assert len(result.citations) == 3
    assert {citation.source_file for citation in result.citations} == {
        "2196-ANSI-Manual-Chinese.pdf"
    }
    assert len(backend.generate_calls) == 1
    _, context, system_prompt = backend.generate_calls[0]
    assert "desmi" not in context.casefold()
    assert "sumit-c4" not in context
    assert "sumit-c1" in context
    assert "只能依据检索到的手册内容回答" in system_prompt
    assert "{context_data}" not in system_prompt
    assert "{content_data}" not in system_prompt


@pytest.mark.asyncio
async def test_naive_query_uses_selected_context_in_generation_prompt(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query("轴承温度过高怎么办？", mode="naive")

    assert result.answer == "应检查轴承润滑状态。"
    assert backend.generate_calls
    assert "pump-p7-c1" in backend.generate_calls[0][1]
    assert "以下是已筛选的手册证据" in backend.generate_calls[0][2]


def _payload(chunks: list[tuple[str, int, str, str]]) -> dict[str, object]:
    rendered = [
        {
            "content": text,
            "file_path": encode_source_ref(Citation(source, page, chunk_id)),
        }
        for source, page, chunk_id, text in chunks
    ]
    return {"status": "success", "data": {"entities": [], "relationships": [], "chunks": rendered}}


@pytest.mark.asyncio
async def test_all_five_supported_modes_are_accepted(tmp_path: Path) -> None:
    expected_modes = ("mix", "hybrid", "local", "global", "naive")
    assert expected_modes == SUPPORTED_QUERY_MODES
    assert "bypass" not in SUPPORTED_QUERY_MODES

    backend = FakeLightRAGBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    for mode in expected_modes:
        result = await service.query("轴承温度过高怎么办？", mode=mode)
        assert result.mode == mode

    assert backend.query_modes == list(expected_modes)


@pytest.mark.asyncio
async def test_service_rejects_modes_outside_the_scoped_five(tmp_path: Path) -> None:
    service = LightRAGService(_settings(tmp_path), backend=FakeLightRAGBackend())
    await service.initialize()

    with pytest.raises(ValueError, match="查询模式"):
        await service.query("测试问题", mode="bypass")  # type: ignore[arg-type]
