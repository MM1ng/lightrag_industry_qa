from __future__ import annotations

import json
from pathlib import Path

import pytest
from industrial_rag.citation_formatter import Citation, encode_source_ref
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService
from industrial_rag.retrieval_trace import TRACE_VERSION, SelectedEvidenceTrace


class TraceBackend:
    def __init__(self) -> None:
        self.query_calls = 0

    async def initialize_storages(self) -> None:
        return None

    async def finalize_storages(self) -> None:
        return None

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        return "unused"

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        return {track_id: "processed"}

    async def aquery_data(self, query: str, param: object) -> dict[str, object]:
        self.query_calls += 1
        first = encode_source_ref(Citation("pump.pdf", 7, "pump-p7-c1"))
        second = encode_source_ref(Citation("pump.pdf", 8, "pump-p8-c1"))
        return {
            "status": "success",
            "data": {
                "chunks": [
                    {
                        "content": "轴承温度过高时检查润滑状态。",
                        "file_path": first,
                        "score": 0.0,
                        "section_path": ["故障处理", "轴承"],
                    },
                    {
                        "content": "轴承温度过高时还应检查冷却水。",
                        "file_path": second,
                        "distance": 0.42,
                    },
                ],
                "references": [],
            },
        }

    async def generate(self, question: str, context: str, system_prompt: str) -> str:
        return "应检查润滑状态和冷却水。"


def _settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
        }
    )


@pytest.mark.asyncio
async def test_query_captures_ordered_trace_from_the_single_real_retrieval_call(
    tmp_path: Path,
) -> None:
    """Catches a second diagnostic retrieval or loss/fabrication of upstream ordering."""
    backend = TraceBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    result = await service.query(" 轴承温度过高怎么办？ ")

    trace = result.retrieval_trace
    assert backend.query_calls == 1
    assert trace.trace_version == TRACE_VERSION
    assert trace.original_query == " 轴承温度过高怎么办？ "
    assert trace.normalized_query == "轴承温度过高怎么办？"
    assert [item.initial_rank for item in trace.initial_results] == [1, 2]
    assert trace.initial_results[0].initial_score == 0.0
    assert trace.initial_results[1].initial_score == 0.42
    assert [item.retrieval_source for item in trace.initial_results] == [
        "lightrag_mix_unspecified",
        "lightrag_mix_unspecified",
    ]
    assert trace.initial_results[0].section_path == ("故障处理", "轴承")
    assert trace.initial_results[0].matched_terms
    assert trace.rerank_applied is False
    assert trace.reranked_results == ()
    assert all(item.reranked_rank is None for item in trace.initial_results)
    assert all(item.reranked_score is None for item in trace.initial_results)
    assert all(isinstance(item, SelectedEvidenceTrace) for item in trace.final_selected_chunks)
    assert [item.final_rank for item in trace.final_selected_chunks] == [1, 2]
    assert [item.initial_rank for item in trace.final_selected_chunks] == [1, 2]
    assert all(item.used_for_answer for item in trace.final_selected_chunks)
    assert all(item.cited_in_answer for item in trace.final_selected_chunks)
    assert trace.selected_chunk_ids == ("pump-p7-c1", "pump-p8-c1")
    assert trace.normalization_ms >= 0
    assert trace.retrieval_ms >= 0
    assert trace.rerank_ms == 0
    assert trace.evidence_selection_ms >= 0


@pytest.mark.asyncio
async def test_trace_serialization_omits_content_prompts_paths_and_credentials(
    tmp_path: Path,
) -> None:
    """Catches accidental persistence of sensitive or full retrieval payload fields."""
    backend = TraceBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()

    trace = (await service.query("轴承温度过高怎么办？")).retrieval_trace
    serialized = json.dumps(trace.to_payload(), ensure_ascii=False)

    assert "轴承温度过高时检查润滑状态" not in serialized
    for forbidden in (
        "Authorization",
        "Bearer ",
        "test-only-key",
        "system_prompt",
        "vector",
        "working_dir",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_trace_enrichment_uses_trusted_document_mapping(tmp_path: Path) -> None:
    """Catches missing document IDs or any attempt to source them from query input."""
    backend = TraceBackend()
    service = LightRAGService(_settings(tmp_path), backend=backend)
    await service.initialize()
    trace = (await service.query("轴承温度过高怎么办？")).retrieval_trace

    enriched = trace.with_document_ids({"pump.pdf": "trusted-document-id"})

    assert {item.document_id for item in enriched.initial_results} == {
        "trusted-document-id"
    }
    assert {item.document_id for item in enriched.final_selected_chunks} == {
        "trusted-document-id"
    }
