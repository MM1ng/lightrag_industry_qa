"""Build the single Phase 4 PyMuPDF index and freeze child retrieval results.

Run once (paid gate required). The resulting index is kept as
``phase4_frozen_index`` for the whole Phase 4; collections are NOT deleted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    EXPANSION_CONFIG,
    FIXED_MODEL,
    GOLDEN_SHA256,
    GOLDEN_SET_PATH,
    PDF_NAMES,
    PYMUPDF_CHILDREN_DIR,
    PROJECT_ROOT,
)

from .config import PROJECT_ROOT  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_inputs() -> dict[str, bool]:
    baseline = json.loads(
        (PROJECT_ROOT / "evaluation/experiments/phase4/baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    checks = {
        "source_phase": baseline["source_phase"] == "Phase 3A-R-Paid",
        "default_parser": baseline["default_parser_pipeline"] == "pymupdf_standard_adapter",
        "golden_set": _sha256_file(GOLDEN_SET_PATH) == GOLDEN_SHA256,
        "p0_results": _sha256_file(PROJECT_ROOT / baseline["p0_results"])
        == baseline["p0_results_sha256"],
        "prompt_bundle": _sha256_file(PROJECT_ROOT / baseline["prompt_bundle"])
        == baseline["prompt_bundle_sha256"],
    }
    return checks


def _load_children() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pdf in PDF_NAMES:
        path = PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


async def _build_index() -> dict[str, Any]:
    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
    from evaluation.experiments.parser_backend.fixed_model_run import _build_kb, _verify_index

    children = _load_children()
    tmp = Path(__file__).resolve().parent / "tmp" / "phase4_index"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=Path(__file__).resolve().parent / "cache" / "phase4_index.jsonl",
        config_hash=EXPANSION_CONFIG["parser_pipeline"],
    )
    world = await _build_kb("0", tmp, children=children, llm=llm)
    check = await _verify_index("phase4", world, expected_children=len(children))
    return {
        "world": world,
        "check": check,
        "llm": llm,
        "children": children,
        "index_llm_summary": llm.summary(),
    }


async def _load_existing_world() -> dict[str, Any]:
    """Resume path: reuse the built phase4_frozen_index without rebuilding."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from industrial_rag.db.models import KnowledgeBase
    from industrial_rag.config import Settings

    tmp = Path(__file__).resolve().parent / "tmp" / "phase4_index"
    _env = {
        "KB_DATA_ROOT": str(tmp / "kb_data"),
        "DATABASE_URL": "sqlite+aiosqlite:///" + (tmp / "exp.db").as_posix(),
        "QDRANT_URL": "http://127.0.0.1:16333",
        "LLM_MODEL": FIXED_MODEL,
        "MODEL_FALLBACK_ENABLED": "false",
        "EMBEDDING_MODEL": "text-embedding-v4",
        "EMBEDDING_DIM": "1024",
        "LIGHTRAG_CHUNK_TOKEN_SIZE": "2000",
        "MINERU_ENABLED": "false",
    }
    for key, value in _env.items():
        os.environ[key] = value
    manifest = json.loads(
        (Path(__file__).resolve().parent / "manifests" / "index_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    _env["QDRANT_COLLECTION_PREFIX"] = manifest["prefix"]
    for key, value in _env.items():
        os.environ[key] = value
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp / 'exp.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        kb = (
            await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.id == manifest["kb_id"])
                .options(selectinload(KnowledgeBase.active_vector_generation))
            )
        ).scalar_one()
    world = {
        "kb": kb,
        "settings": Settings.from_env(),
        "factory": factory,
        "engine": engine,
        "prefix": manifest["prefix"],
        "tmp": tmp,
    }
    return {
        "world": world,
        "check": {"names": manifest["collections"], "counts": manifest["points"]},
        "llm": None,
        "children": _load_children(),
        "index_llm_summary": manifest["index_llm_summary"],
        "resumed": True,
    }


async def _freeze_child_results(build: dict[str, Any]) -> Path:
    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
    from evaluation.experiments.parser_backend.fixed_model_run import _extract_retrieved
    from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
    from industrial_rag.lightrag_service import LightRAGService, QueryOptions
    from evaluation.experiments.parser_backend.metrics import load_gold
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    world = build["world"]
    kb_settings = settings_for_knowledge_base(world["settings"], world["kb"])
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=Path(__file__).resolve().parent / "cache" / "phase4_retrieval.jsonl",
        config_hash=EXPANSION_CONFIG["parser_pipeline"],
    )
    import functools

    import industrial_rag.lightrag_service as service_module

    service_module.build_official_backend = functools.partial(
        service_module.build_official_backend, llm_model_func=llm
    )
    service = LightRAGService(kb_settings)
    await service.initialize()

    child_meta: dict[str, dict[str, Any]] = {}
    for child in build["children"]:
        text = str(child.get("embedding_content") or child.get("content") or "")
        child_meta[child["chunk_id"]] = {
            "parent_id": child.get("parent_chunk_id", ""),
            "document_id": child.get("document_name", ""),
            "page": child.get("page_start"),
            "child_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    gold = load_gold()
    rows: list[dict[str, Any]] = []
    try:
        for case in gold:
            evidence = await service._backend.aquery_data(
                case.question,
                QueryOptions(
                    mode=EXPANSION_CONFIG["query_mode"],
                    top_k=EXPANSION_CONFIG["top_k"],
                    chunk_top_k=EXPANSION_CONFIG["chunk_top_k"],
                    enable_rerank=EXPANSION_CONFIG["rerank"],
                ),
            )
            retrieved = _extract_retrieved(evidence)
            for item in retrieved:
                meta = child_meta.get(item.get("chunk_id", ""), {})
                rows.append(
                    {
                        "question_id": case.case_id,
                        "question": case.question,
                        "primary_category": QUESTION_CATEGORIES.get(case.case_id, "未分类"),
                        "child_chunk_id": item.get("chunk_id"),
                        "parent_id": meta.get("parent_id", ""),
                        "document_id": meta.get("document_id", item.get("file")),
                        "page": meta.get("page", item.get("page")),
                        "rank": item.get("rank"),
                        "retrieval_score": item.get("score"),
                        "child_text_hash": meta.get("child_text_hash", ""),
                        "query_mode": EXPANSION_CONFIG["query_mode"],
                        "top_k": EXPANSION_CONFIG["top_k"],
                        "chunk_top_k": EXPANSION_CONFIG["chunk_top_k"],
                    }
                )
    finally:
        await service.close()
    out = Path(__file__).resolve().parent / "frozen_child_results.jsonl"
    out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print("frozen_child_results rows:", len(rows))
    print("sha256:", _sha256_file(out))
    return out


async def _run_all() -> int:
    manifest_path = Path(__file__).resolve().parent / "manifests" / "index_manifest.json"
    if manifest_path.is_file() and (Path(__file__).resolve().parent / "tmp" / "phase4_index" / "exp.db").is_file():
        print("resuming from existing phase4_frozen_index")
        build = await _load_existing_world()
    else:
        print("building phase4_frozen_index")
        build = await _build_index()
    world = build["world"]
    kb = world["kb"]
    index_manifest = {
        "index_role": "phase4_frozen_index",
        "parser_pipeline": "pymupdf_standard_adapter",
        "kb_id": kb.id,
        "generation": kb.active_vector_generation.generation,
        "prefix": world["prefix"],
        "collections": build["check"]["names"],
        "points": build["check"]["counts"],
        "children_count": len(build["children"]),
        "index_llm_summary": build["index_llm_summary"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "experiment_only": True,
    }
    manifests = Path(__file__).resolve().parent / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "index_manifest.json").write_text(
        json.dumps(index_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index_manifest, ensure_ascii=False, indent=2))
    result_path = await _freeze_child_results(build)
    print("result path:", result_path)
    return 0


def main() -> int:
    checks = verify_frozen_inputs()
    if not all(checks.values()):
        print("frozen input verification FAILED:", checks)
        return 1
    if os.environ.get("IRA_PHASE3A_PAID_RUN") != "1":
        print("IRA_PHASE3A_PAID_RUN != 1; refusing index build")
        return 1
    if os.environ.get("LLM_MODEL") != FIXED_MODEL:
        print("LLM_MODEL is not qwen-plus-2025-07-28")
        return 1
    if os.environ.get("MODEL_FALLBACK_ENABLED", "true").lower() != "false":
        print("MODEL_FALLBACK_ENABLED must be false")
        return 1
    print("frozen inputs verified:", checks)
    return asyncio.run(_run_all())


if __name__ == "__main__":
    sys.exit(main())
