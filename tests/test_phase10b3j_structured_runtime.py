from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from industrial_rag.citation_formatter import Citation, encode_source_ref
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, LightRAGService


class StructuredBackend:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.generate_calls: list[dict[str, Any]] = []
        self.query_calls = 0

    async def initialize_storages(self) -> None:
        return None

    async def finalize_storages(self) -> None:
        return None

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        return "track"

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        return {track_id: "processed"}

    async def aquery_data(self, query: str, param: object) -> dict[str, object]:
        self.query_calls += 1
        source = encode_source_ref(Citation("manual.pdf", 9, "child-1"))
        return {
            "status": "success",
            "data": {
                "entities": [],
                "relationships": [],
                "chunks": [
                    {"content": "轴承架油位应保持在观察窗中间位置。", "file_path": source}
                ],
                "references": [{"file_path": source}],
            },
        }

    async def generate(
        self, question: str, context: str, system_prompt: str, **kwargs: object
    ) -> str:
        self.generate_calls.append(
            {"question": question, "context": context, "system_prompt": system_prompt, **kwargs}
        )
        return self.reply


def _settings(tmp_path: Path, *, enabled: bool) -> Settings:
    return Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "LLM_MODEL": "qwen-plus-2025-07-28",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
            "QDRANT_GENERATION": "g1",
            "QA_STRUCTURED_CITATION_OUTPUT_ENABLED": str(enabled).lower(),
        }
    )


@pytest.mark.asyncio
async def test_valid_structured_output_calls_backend_once_and_maps_child(tmp_path: Path) -> None:
    backend = StructuredBackend(
        '{"status":"success","answer_points":[{"text":"油位应在观察窗中间。",'
        '"source_ids":["S1"]}],"unresolved_requirement_ids":[]}'
    )
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert len(backend.generate_calls) == 1
    assert backend.generate_calls[0]["response_format"] == {"type": "json_object"}
    assert "请只输出合法JSON" in backend.generate_calls[0]["system_prompt"]
    assert result.answer_points[0].evidence_ids == ("E1",)
    assert result.citations == (Citation("manual.pdf", 9, "child-1"),)
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_output_valid is True
    assert result.retrieval_trace.backend_generate_call_count == 1
    assert backend.query_calls == 1


@pytest.mark.asyncio
async def test_invalid_source_uses_j0_postprocessing_without_second_generation(tmp_path: Path) -> None:
    backend = StructuredBackend(
        '{"status":"success","answer_points":[{"text":"保留文本。",'
        '"source_ids":["S9"]}],"unresolved_requirement_ids":[]}'
    )
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert result.answer == "保留文本。"
    assert len(backend.generate_calls) == 1
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_fallback is True
    assert (
        result.retrieval_trace.structured_citation_fallback_mode
        == "fallback_to_j0_postprocessing"
    )


@pytest.mark.asyncio
async def test_unparseable_output_is_safe_without_second_generation(tmp_path: Path) -> None:
    backend = StructuredBackend("not json")
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.answer_status == "insufficient_evidence"
    assert len(backend.generate_calls) == 1
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_fallback is True
    assert (
        result.retrieval_trace.structured_citation_fallback_mode
        == "safe_failure_no_second_generation"
    )


@pytest.mark.asyncio
async def test_flag_off_keeps_existing_non_json_prompt_and_result_shape(tmp_path: Path) -> None:
    backend = StructuredBackend("原J0回答。")
    service = LightRAGService(_settings(tmp_path, enabled=False), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert len(backend.generate_calls) == 1
    assert "response_format" not in backend.generate_calls[0]
    assert "请只输出合法JSON" not in backend.generate_calls[0]["system_prompt"]
    assert result.answer == "原J0回答。"
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_flag is False
