"""Run an offline fake smoke test, or real LightRAG queries with --real."""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.citation_formatter import Citation, encode_source_ref  # noqa: E402
from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.document_parser import DocumentChunk  # noqa: E402
from industrial_rag.lightrag_service import (  # noqa: E402
    INSUFFICIENT_EVIDENCE_MESSAGE,
    LightRAGService,
)

REAL_QUESTIONS = (
    "离心泵启动前需要检查什么？",
    "轴承温度过高可能是什么原因？",
    "水泵不输送液体应该如何排查？",
)
NO_ANSWER_QUESTION = "手册是否规定了离心泵在火星基地零重力环境下的维护周期？"


class _FakeBackend:
    async def initialize_storages(self) -> None:
        return None

    async def finalize_storages(self) -> None:
        return None

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        if len(input) != 1 or not kwargs.get("file_paths"):
            raise RuntimeError("fake insert contract failed")
        return "offline-track"

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        return {track_id: "processed"}

    async def aquery_data(self, query: str, param: object) -> dict[str, object]:
        if query == NO_ANSWER_QUESTION:
            return {"status": "failure", "data": {}}
        source = encode_source_ref(Citation("offline-manual.pdf", 2, "offline-p2-c1"))
        return {
            "status": "success",
            "data": {
                "entities": [],
                "relationships": [],
                "chunks": [{"content": "启动前检查阀门。", "file_path": source}],
                "references": [{"file_path": source}],
            },
        }

    async def aquery(self, query: str, param: object, system_prompt: str) -> str:
        return "启动前应检查阀门状态。"


async def _offline_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="industrial-rag-smoke-") as directory:
        settings = Settings.from_mapping(
            {
                "DASHSCOPE_API_KEY": "offline-not-a-secret",
                "LIGHTRAG_WORKING_DIR": directory,
            }
        )
        service = LightRAGService(settings, backend=_FakeBackend())
        await service.initialize()
        track_id = await service.ingest(
            [
                DocumentChunk(
                    chunk_id="offline-p2-c1",
                    text="启动前检查阀门。",
                    source_file="offline-manual.pdf",
                    page_number=2,
                    section_title="启动检查",
                )
            ]
        )
        result = await service.query(REAL_QUESTIONS[0])
        no_answer = await service.query(NO_ANSWER_QUESTION, mode="naive")
        await service.close()
        if not result.citations or no_answer.answer != INSUFFICIENT_EVIDENCE_MESSAGE:
            raise RuntimeError("offline query contract failed")
        print(f"PASS offline initialize/insert track_id={track_id}")
        print(f"PASS offline query citation={result.citations[0].display}")
        print("PASS offline no-evidence response")


async def _real_smoke() -> None:
    settings = Settings.from_env()
    service = LightRAGService(settings)
    try:
        await service.initialize()
        print("PASS real LightRAG initialized")
        for number, question in enumerate(REAL_QUESTIONS, start=1):
            result = await service.query(question, mode="mix")
            if result.answer == INSUFFICIENT_EVIDENCE_MESSAGE or not result.citations:
                raise RuntimeError(f"真实查询 {number} 未返回充分依据和页码引用")
            print(
                f"PASS real query={number} citations={','.join(c.display for c in result.citations)}"
            )
            print(f"ANSWER {number}: {result.answer}")
        no_answer = await service.query(NO_ANSWER_QUESTION, mode="naive")
        safe_no_answer = no_answer.answer == INSUFFICIENT_EVIDENCE_MESSAGE or any(
            phrase in no_answer.answer for phrase in ("未检索到", "未提供", "没有提及", "无法")
        )
        if not safe_no_answer:
            raise RuntimeError("无答案问题未明确说明手册依据不足")
        print(f"PASS real no-evidence answer={no_answer.answer}")
    finally:
        await service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="调用真实百炼 API 和已有索引")
    args = parser.parse_args()
    asyncio.run(_real_smoke() if args.real else _offline_smoke())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
