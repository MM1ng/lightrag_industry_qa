"""Build isolated Qdrant experiment KBs and run the 50-question golden set.

P0/P1 each get one throwaway KB (both PDFs), a temp SQLite database, a random
Qdrant prefix and a fresh generation workspace. Retrieval uses the locked LLM
for both groups; LLM/embedding calls are counted.

Usage (after parse_p0/parse_p1):
    python -m evaluation.experiments.parser_backend.index_retrieve --group 0
    python -m evaluation.experiments.parser_backend.index_retrieve --group 1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .common import read_jsonl, write_json, write_jsonl
from .config import (
    GOLDEN_SET_PATH,
    LLM_LOCK,
    PDF_NAMES,
    QDRANT_TEST_URL,
    RETRIEVAL,
    comparison_dir,
    retrieval_dir,
)
from .metrics import (
    build_evidence_mapping,
    category_breakdown,
    citation_metrics,
    load_gold,
    retrieval_metrics,
)


def _bootstrap_settings(group: str, tmp: Path) -> None:
    """Point a fresh process-level env at the throwaway experiment workspace."""
    prefix = f"ira_p3a_{secrets.token_hex(4)}"
    os.environ["KB_DATA_ROOT"] = str(tmp / "kb_data")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (tmp / "exp.db").as_posix()
    os.environ["QDRANT_URL"] = QDRANT_TEST_URL
    os.environ["QDRANT_COLLECTION_PREFIX"] = prefix
    os.environ["LLM_MODEL"] = LLM_LOCK["llm_model"]
    os.environ["LLM_FALLBACK_MODELS"] = ",".join(LLM_LOCK["llm_fallback_models"])
    os.environ["EMBEDDING_MODEL"] = LLM_LOCK["embedding_model"]
    os.environ["EMBEDDING_DIM"] = str(LLM_LOCK["embedding_dim"])
    os.environ["LIGHTRAG_CHUNK_TOKEN_SIZE"] = "2000"
    os.environ["MINERU_ENABLED"] = "false"


def _write_parsed_artifacts(parsed_dir: Path, doc_id: str, child_rows: list[dict[str, Any]]) -> Path:
    current = parsed_dir / "documents" / doc_id / "current"
    current.mkdir(parents=True, exist_ok=True)
    write_jsonl(current / "child_chunks.jsonl", child_rows)
    return current


async def _build_kb(group: str, tmp: Path, *, cache_src: Path | None = None) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from industrial_rag.api import create_app
    from industrial_rag.db.models import TaskType
    from industrial_rag.db.session import init_db, reset_for_testing
    from industrial_rag.repositories.document_repository import DocumentRepository
    from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from industrial_rag.repositories.task_repository import TaskRepository
    from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager
    from industrial_rag.services.task_context import TaskExecutionContext
    from industrial_rag.storage_layout import kb_parsed_dir

    _bootstrap_settings(group, tmp)
    from industrial_rag.config import Settings

    settings = Settings.from_env()
    runtime_manager = KnowledgeBaseRuntimeManager()
    reset_for_testing()
    await init_db(drop_all=True)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp / 'exp.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Create KB through the real API.
    app = create_app(settings=settings)
    app.state.service_api_key = None
    app.state.runtime = None
    app.state.resolved_settings = settings
    app.state.runtime_manager = runtime_manager
    app.state.task_executor = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/knowledge-bases", json={"name": f"P{group}_pymupdf_qdrant" if group == "0" else "P1_mineru_qdrant"}
        )
        assert response.status_code == 201, response.text
        kb_id = response.json()["id"]

    # Create document rows + write child artifacts for both PDFs.
    doc_ids: dict[str, str] = {}
    async with factory() as session:
        repo = DocumentRepository(session)
        for pdf_name in PDF_NAMES:
            pdf_path = Path(r"D:\industrial_energy_agent\data\manuals") / pdf_name
            doc = await repo.create(
                knowledge_base_id=kb_id,
                original_file_name=pdf_name,
                stored_file_name=pdf_name,
                file_path=str(pdf_path),
                file_hash=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                file_size=pdf_path.stat().st_size,
                mime_type="application/pdf",
            )
            doc_ids[pdf_name] = doc.id
        await session.commit()

    async with factory() as session:
        repo = DocumentRepository(session)
        for pdf_name in PDF_NAMES:
            doc = await repo.get(doc_ids[pdf_name])
            rows = read_jsonl(
                Path(r"D:\industrial_energy_agent\evaluation\experiments\parser_backend")
                / f"P{group}"
                / pdf_name
                / "child_chunks.jsonl"
            )
            _write_parsed_artifacts(kb_parsed_dir(kb_id), doc.id, rows)
            await session.commit()

    # Run the real migrate_to_qdrant handler (shadow build + activation).
    import industrial_rag.services.handler_impls  # noqa: F401
    from industrial_rag.services.task_handlers import get_builtin_registry

    if cache_src is not None:
        import industrial_rag.lightrag_service as lightrag_service_module

        _original_initialize = lightrag_service_module.LightRAGService.initialize

        async def _initialize_with_cache(self) -> None:
            from industrial_rag.config import write_storage_metadata

            token = self.settings.vector_workspace
            target = (
                self.settings.working_dir / token
                if token
                else self.settings.working_dir
            )
            target.mkdir(parents=True, exist_ok=True)
            dest = target / "kv_store_llm_response_cache.json"
            if not dest.exists():
                dest.write_bytes(cache_src.read_bytes())
                print(f"[cache] seeded {dest.stat().st_size} bytes from {cache_src.name}")
            write_storage_metadata(
                self.settings.working_dir,
                self.settings.embedding_model,
                self.settings.embedding_dim,
            )
            return await _original_initialize(self)

        lightrag_service_module.LightRAGService.initialize = _initialize_with_cache
        print(f"[cache] will reuse {cache_src} for group {group}")

    async with factory() as session:
        task = await TaskRepository(session).create(knowledge_base_id=kb_id, task_type=TaskType.migrate_to_qdrant)
        await session.commit()
        task_repo = TaskRepository(session)
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = DocumentRepository(session)
        claimed = await task_repo.mark_running(task.id)
        assert claimed is not None
        await session.commit()
        ctx = TaskExecutionContext(
            task=claimed,
            kb_repo=kb_repo,
            doc_repo=doc_repo,
            task_repo=task_repo,
            runtime_manager=runtime_manager,
            settings=settings,
        )
        handler = get_builtin_registry().get(TaskType.migrate_to_qdrant)
        result = await handler(ctx)
        await session.commit()
    assert result.success, f"index failed: {result.error_code}: {result.error_message}"
    await runtime_manager.close_all()

    async with factory() as session:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from industrial_rag.db.models import KnowledgeBase

        kb = (
            await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.id == kb_id)
                .options(selectinload(KnowledgeBase.active_vector_generation))
            )
        ).scalar_one()
    return {"kb": kb, "settings": settings, "factory": factory, "engine": engine, "prefix": os.environ["QDRANT_COLLECTION_PREFIX"], "tmp": tmp}


def _count_calls():
    """Wrap LightRAG's openai helpers so every call is counted."""
    import lightrag.llm.openai as openai_module

    counters = {"llm": 0, "embedding": 0}
    original_llm = openai_module.openai_complete_if_cache
    original_embed = openai_module.openai_embed.func

    async def counted_llm(*args, **kwargs):
        counters["llm"] += 1
        return await original_llm(*args, **kwargs)

    async def counted_embed(*args, **kwargs):
        counters["embedding"] += 1
        return await original_embed(*args, **kwargs)

    openai_module.openai_complete_if_cache = counted_llm
    openai_module.openai_embed.func = counted_embed
    return counters, (original_llm, original_embed)


def _restore_calls(originals) -> None:
    import lightrag.llm.openai as openai_module

    openai_module.openai_complete_if_cache = originals[0]
    openai_module.openai_embed.func = originals[1]


def _extract_retrieved(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    from industrial_rag.citation_formatter import collect_citations

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
            out.append(
                {
                    "file": citation.source_file,
                    "page": citation.page_number,
                    "chunk_id": citation.chunk_id,
                    "score": value.get("score") or value.get("distance"),
                    "rank": len(out) + 1,
                }
            )
    return out


async def _retrieve(world: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        LightRAGService,
        QueryOptions,
        _generation_system_prompt,
        _selected_context,
    )
    from industrial_rag.evidence_policy import select_evidence

    kb = world["kb"]
    base = world["settings"]
    kb_settings = settings_for_knowledge_base(base, kb)
    counters, originals = _count_calls()
    rows: list[dict[str, Any]] = []
    index_started = time.monotonic()
    try:
        service = LightRAGService(kb_settings)
        await service.initialize()
        gold = load_gold()
        for case in gold:
            started = time.monotonic()
            evidence = await service._backend.aquery_data(
                case.question,
                QueryOptions(
                    mode=RETRIEVAL["mode"],
                    top_k=RETRIEVAL["top_k"],
                    chunk_top_k=RETRIEVAL["chunk_top_k"],
                    enable_rerank=RETRIEVAL["enable_rerank"],
                ),
            )
            retrieved = _extract_retrieved(evidence)
            decision = select_evidence(case.question, evidence, limit=RETRIEVAL["evidence_limit"])
            if decision.allowed:
                context = _selected_context(decision.selected)
                answer = (
                    await service._backend.generate(case.question, context, _generation_system_prompt(context))
                ).strip()
            else:
                answer = INSUFFICIENT_EVIDENCE_MESSAGE
            latency_ms = round((time.monotonic() - started) * 1000, 3)
            rows.append(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "expects_evidence": case.expects_evidence,
                    "retrieved": retrieved,
                    "citations": [
                        {
                            "source_file": c.citation.source_file,
                            "page_number": c.citation.page_number,
                            "chunk_id": c.citation.chunk_id,
                        }
                        for c in decision.selected
                    ],
                    "answer": answer,
                    "refused": answer == INSUFFICIENT_EVIDENCE_MESSAGE,
                    "latency_ms": latency_ms,
                }
            )
        await service.close()
    finally:
        _restore_calls(originals)
    index_seconds = round(time.monotonic() - index_started, 3)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "results.jsonl", rows)
    perf = {
        "llm_calls": counters["llm"],
        "embedding_calls": counters["embedding"],
        "total_query_seconds": index_seconds,
        "avg_query_ms": round(sum(r["latency_ms"] for r in rows) / len(rows), 1) if rows else 0,
        "latency_p50_ms": sorted(r["latency_ms"] for r in rows)[len(rows) // 2] if rows else 0,
        "latency_p95_ms": sorted(r["latency_ms"] for r in rows)[int(len(rows) * 0.95)] if rows else 0,
    }
    write_json(out_dir / "perf.json", perf)
    return {"rows": rows, "perf": perf, "kb": kb, "settings": kb_settings}


def _evidence_mapping(group: str) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for pdf_name in PDF_NAMES:
        children.extend(
            read_jsonl(
                Path(r"D:\industrial_energy_agent\evaluation\experiments\parser_backend")
                / f"P{group}"
                / pdf_name
                / "child_chunks.jsonl"
            )
        )
    return build_evidence_mapping(children)


def _metrics(group: str, rows: list[dict[str, Any]], mapping: dict[str, Any], perf: dict[str, Any]) -> dict[str, Any]:
    gold = load_gold()
    retrieval = retrieval_metrics(rows, gold=gold, mapping=mapping)
    citations = citation_metrics(rows, gold=gold)
    categories = category_breakdown(rows, retrieval)
    return {
        "group": group,
        "retrieval": retrieval,
        "citations": citations,
        "categories": categories,
        "perf": perf,
    }


async def run_group(group: str, *, cleanup: bool = True) -> dict[str, Any]:
    import shutil

    experiment_root = Path(r"D:\industrial_energy_agent\evaluation\experiments\parser_backend")
    tmp = experiment_root / "tmp" / f"run_{group}"
    cache_src: Path | None = None
    if tmp.exists():
        found = list(tmp.rglob("kv_store_llm_response_cache.json"))
        if found:
            best = max(found, key=lambda p: p.stat().st_size)
            safe_copy = experiment_root / "tmp" / f"llm_cache_p{group}.json"
            safe_copy.parent.mkdir(parents=True, exist_ok=True)
            safe_copy.write_bytes(best.read_bytes())
            cache_src = safe_copy
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    world = await _build_kb(group, tmp, cache_src=cache_src)
    out_dir = retrieval_dir(group)
    result = await _retrieve(world, out_dir)
    mapping = _evidence_mapping(group)
    write_json(comparison_dir() / f"evidence_mapping_p{group}.json", mapping)
    metrics = _metrics(group, result["rows"], mapping, result["perf"])
    write_json(out_dir / "metrics.json", metrics)

    kb = result["kb"]
    from industrial_rag.config import Settings
    from industrial_rag.services.qdrant_collection_service import QdrantCollectionService

    qdrant_settings = Settings(
        api_key="experiment",
        vector_backend="qdrant",
        qdrant_url=QDRANT_TEST_URL,
        qdrant_collection_prefix=world["prefix"],
        qdrant_kb_id=kb.id,
        qdrant_generation=kb.active_vector_generation.generation,
    )
    service = QdrantCollectionService(qdrant_settings)
    counts = await service.verify_generation()
    metrics["qdrant_points"] = counts
    write_json(out_dir / "metrics.json", metrics)
    print(f"[group {group}] qdrant chunks points={counts} llm_calls={result['perf']['llm_calls']}")
    if cleanup:
        await service.delete_generation()
        print(f"[group {group}] exact collections deleted")
    await world["engine"].dispose()
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=["0", "1"])
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    metrics = asyncio.run(run_group(args.group, cleanup=not args.no_cleanup))
    print(json.dumps(metrics["retrieval"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
