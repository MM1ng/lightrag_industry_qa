"""Fixed-model fair P0/P1 experiment runner (Phase 3A-R).

Usage:
    python -m evaluation.experiments.parser_backend.fixed_model_run --precheck
    python -m evaluation.experiments.parser_backend.fixed_model_run --full --group 0
    python -m evaluation.experiments.parser_backend.fixed_model_run --full --group 1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .common import read_jsonl, write_json, write_jsonl
from .config import PDF_NAMES, PROJECT_ROOT, QDRANT_TEST_URL
from .config import QUESTION_CATEGORIES
from .fixed_model_gate import assert_consistency, load_frozen_config
from .fixed_model_llm import FixedModelLLM
from .paid_run_gate import FIXED_MODEL, check_paid_run_gate
from .metrics import (
    build_evidence_mapping,
    category_breakdown,
    citation_metrics,
    load_gold,
    retrieval_metrics,
)

EXPERIMENT_ROOT = Path(__file__).resolve().parent
FIXED_DIR = EXPERIMENT_ROOT / "fixed_model"

PREVIEW_QUESTIONS = ["S001", "S007", "S011", "S015"]


def _child_dir(group: str) -> Path:
    if group == "0":
        return EXPERIMENT_ROOT / "P0"
    return FIXED_DIR / "P1_mineru"


def _all_children(group: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pdf in PDF_NAMES:
        rows.extend(read_jsonl(_child_dir(group) / pdf / "child_chunks.jsonl"))
    return rows


def _select_sample(group: str, size: int = 20, seed: int = 20260801) -> list[dict[str, Any]]:
    """Deterministic stratified sample covering content categories and pages."""
    import random

    rows = _all_children(group)
    rng = random.Random(seed)
    rng.shuffle(rows)
    wanted = ["parameter_table", "operation_steps", "safety_warning", "fault_diagnosis", "normal_text"]
    selected: list[dict[str, Any]] = []
    for ctype in wanted:
        pool = [r for r in rows if r.get("content_type") == ctype and r not in selected]
        count = 4 if ctype in ("parameter_table", "operation_steps") else 3
        selected.extend(pool[:count])
    if len(selected) < size:
        rest = [r for r in rows if r not in selected]
        selected.extend(rest[: size - len(selected)])
    return selected[:size]


def _bootstrap_env(tmp: Path, prefix: str) -> None:
    cfg = load_frozen_config()
    os.environ["KB_DATA_ROOT"] = str(tmp / "kb_data")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (tmp / "exp.db").as_posix()
    os.environ["QDRANT_URL"] = QDRANT_TEST_URL
    os.environ["QDRANT_COLLECTION_PREFIX"] = prefix
    os.environ["LLM_MODEL"] = cfg["llm_model"]
    # Fallback is disabled; this value is never called but must differ from the
    # primary model to satisfy Settings validation.
    os.environ["LLM_FALLBACK_MODELS"] = "qwen3.5-flash-2026-02-23"
    os.environ["MODEL_FALLBACK_ENABLED"] = "false"
    os.environ["EMBEDDING_MODEL"] = cfg["embedding_model"]
    os.environ["EMBEDDING_DIM"] = str(cfg["embedding_dimension"])
    os.environ["LIGHTRAG_CHUNK_TOKEN_SIZE"] = str(cfg["chunk_token_size"])
    os.environ["MINERU_ENABLED"] = "false"


def _patch_build_official_backend(llm: FixedModelLLM) -> None:
    import functools

    import industrial_rag.lightrag_service as module

    module.build_official_backend = functools.partial(
        module.build_official_backend, llm_model_func=llm
    )


async def _build_kb(
    group: str,
    tmp: Path,
    *,
    children: list[dict[str, Any]],
    llm: FixedModelLLM,
) -> dict[str, Any]:
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

    _bootstrap_env(tmp, f"ira_p3ar_{secrets.token_hex(4)}")
    from industrial_rag.config import Settings

    settings = Settings.from_env()
    runtime_manager = KnowledgeBaseRuntimeManager()
    reset_for_testing()
    await init_db(drop_all=True)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp / 'exp.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app(settings=settings)
    app.state.service_api_key = None
    app.state.runtime = None
    app.state.resolved_settings = settings
    app.state.runtime_manager = runtime_manager
    app.state.task_executor = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/knowledge-bases",
            json={"name": "P0_pymupdf_qdrant" if group == "0" else "P1_mineru_clean_qdrant"},
        )
        assert response.status_code == 201, response.text
        kb_id = response.json()["id"]

    by_pdf: dict[str, list[dict[str, Any]]] = {}
    for child in children:
        by_pdf.setdefault(child.get("document_name", ""), []).append(child)

    async with factory() as session:
        repo = DocumentRepository(session)
        for pdf_name, pdf_children in by_pdf.items():
            pdf_path = PROJECT_ROOT / "data" / "manuals" / pdf_name
            doc = await repo.create(
                knowledge_base_id=kb_id,
                original_file_name=pdf_name,
                stored_file_name=pdf_name,
                file_path=str(pdf_path),
                file_hash=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                file_size=pdf_path.stat().st_size,
                mime_type="application/pdf",
            )
            current = kb_parsed_dir(kb_id) / "documents" / doc.id / "current"
            current.mkdir(parents=True, exist_ok=True)
            write_jsonl(current / "child_chunks.jsonl", pdf_children)
        await session.commit()

    import industrial_rag.services.handler_impls  # noqa: F401
    from industrial_rag.services.task_handlers import get_builtin_registry

    _patch_build_official_backend(llm)
    async with factory() as session:
        task = await TaskRepository(session).create(knowledge_base_id=kb_id, task_type=TaskType.migrate_to_qdrant)
        await session.commit()
        claimed = await task_repo_mark_running(TaskRepository(session), task.id)
        ctx = TaskExecutionContext(
            task=claimed,
            kb_repo=KnowledgeBaseRepository(session),
            doc_repo=DocumentRepository(session),
            task_repo=TaskRepository(session),
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
    return {
        "kb": kb,
        "settings": settings,
        "factory": factory,
        "engine": engine,
        "prefix": os.environ["QDRANT_COLLECTION_PREFIX"],
        "tmp": tmp,
    }


async def task_repo_mark_running(task_repo, task_id: str):
    claimed = await task_repo.mark_running(task_id)
    assert claimed is not None
    return claimed


async def _verify_index(group: str, world: dict[str, Any], expected_children: int) -> dict[str, Any]:
    from industrial_rag.config import Settings
    from industrial_rag.services.qdrant_collection_service import QdrantCollectionService

    kb = world["kb"]
    qdrant_settings = Settings(
        api_key="experiment",
        vector_backend="qdrant",
        qdrant_url=QDRANT_TEST_URL,
        qdrant_collection_prefix=world["prefix"],
        qdrant_kb_id=kb.id,
        qdrant_generation=kb.active_vector_generation.generation,
    )
    service = QdrantCollectionService(qdrant_settings)
    names = service.names()
    client = service._client()
    try:
        counts = {}
        for namespace, name in names.items():
            counts[namespace] = (await client.count(name, exact=True)).count
    finally:
        await client.close()
    checks = {
        "chunks_non_empty": counts["chunks"] > 0,
        "entities_non_empty": counts["entities"] > 0,
        "relationships_non_empty": counts["relationships"] > 0,
        "chunks_point_ge_children": counts["chunks"] >= expected_children,
    }
    assert all(checks.values()), f"index completeness failed: {checks} counts={counts}"
    return {"names": names, "counts": counts}


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


async def _retrieve_questions(
    world: dict[str, Any],
    llm: FixedModelLLM,
    *,
    question_ids: list[str] | None = None,
    out_jsonl: Path,
) -> list[dict[str, Any]]:
    from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        LightRAGService,
        QueryOptions,
        _generation_system_prompt,
        _selected_context,
    )
    from industrial_rag.evidence_policy import select_evidence

    cfg = load_frozen_config()
    kb_settings = settings_for_knowledge_base(world["settings"], world["kb"])
    service = LightRAGService(kb_settings)
    await service.initialize()
    gold = load_gold()
    gold_by_id = {case.case_id: case for case in gold}
    selected = [gold_by_id[qid] for qid in question_ids] if question_ids else list(gold)
    rows: list[dict[str, Any]] = []
    try:
        for case in selected:
            start_calls = len(llm.calls)
            started = time.monotonic()
            evidence = await service._backend.aquery_data(
                case.question,
                QueryOptions(
                    mode=cfg["query_mode"],
                    top_k=cfg["top_k"],
                    chunk_top_k=cfg["chunk_top_k"],
                    enable_rerank=cfg["enable_rerank"],
                ),
            )
            retrieved = _extract_retrieved(evidence)
            decision = select_evidence(case.question, evidence, limit=cfg["evidence_limit"])
            if decision.allowed:
                context = _selected_context(decision.selected)
                answer = (
                    await service._backend.generate(case.question, context, _generation_system_prompt(context))
                ).strip()
            else:
                answer = INSUFFICIENT_EVIDENCE_MESSAGE
            latency_ms = round((time.monotonic() - started) * 1000, 3)
            query_calls = llm.calls[start_calls:]
            rows.append(
                {
                    "question_id": case.case_id,
                    "category": "?",  # filled by metric layer
                    "question": case.question,
                    "requested_model": cfg["query_llm_model"],
                    "actual_model": sorted({c["actual_model"] for c in query_calls}),
                    "query_mode": cfg["query_mode"],
                    "top_k": cfg["top_k"],
                    "chunk_top_k": cfg["chunk_top_k"],
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
                    "status": "ok",
                    "latency": latency_ms,
                    "retry_count": sum(c["retry_count"] for c in query_calls),
                    "input_tokens": sum(c["input_tokens"] for c in query_calls),
                    "output_tokens": sum(c["output_tokens"] for c in query_calls),
                    "total_tokens": sum(c["total_tokens"] for c in query_calls),
                    "error": None,
                }
            )
    finally:
        await service.close()
    write_jsonl(out_jsonl, rows)
    return rows


def _percentile(values: list[int | float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * pct))])


def _estimate(cfg: dict[str, Any], index_stats: dict[str, Any], query_stats: dict[str, Any]) -> dict[str, Any]:
    """Extrapolate full-experiment token needs from the precheck samples."""
    p0_full_children = len(_all_children("0"))
    p1_full_children = len(_all_children("1"))
    per_chunk = {
        group: index_stats[group]["total_tokens"] / max(1, index_stats[group]["chunks"])
        for group in ("0", "1")
    }
    per_question = {
        group: query_stats[group]["total_tokens"] / max(1, query_stats[group]["questions"])
        for group in ("0", "1")
    }
    p0_index = per_chunk["0"] * p0_full_children
    p1_index = per_chunk["1"] * p1_full_children
    p0_query = per_question["0"] * 50
    p1_query = per_question["1"] * 50
    merge = (p0_index + p1_index) * 0.08
    base = p0_index + p1_index + p0_query + p1_query + merge
    total = base * 1.2
    return {
        "p0_full_children": p0_full_children,
        "p1_full_children": p1_full_children,
        "per_chunk_tokens": {k: round(v, 1) for k, v in per_chunk.items()},
        "per_question_tokens": {k: round(v, 1) for k, v in per_question.items()},
        "p0_estimated_index_tokens": round(p0_index),
        "p1_estimated_index_tokens": round(p1_index),
        "p0_estimated_query_tokens": round(p0_query),
        "p1_estimated_query_tokens": round(p1_query),
        "merge_tokens": round(merge),
        "estimated_total_tokens": round(total),
        "with_20pct_margin": True,
        "decision": "proceed" if total <= 800_000 else (
            "high_quota_risk" if total <= 1_000_000 else "blocked_insufficient_quota"
        ),
    }


async def run_precheck() -> dict[str, Any]:
    gate = assert_consistency()
    cfg = load_frozen_config()
    llm = FixedModelLLM(
        model=cfg["llm_model"],
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=cfg["enable_thinking"],
        cache_path=FIXED_DIR / "cache" / "precheck.jsonl",
        config_hash=assert_consistency()["p0"]["chunk_config_hash"],
    )
    index_stats: dict[str, Any] = {}
    query_stats: dict[str, Any] = {}
    for group in ("0", "1"):
        group_start_calls = len(llm.calls)
        sample = _select_sample(group, size=20)
        tmp = EXPERIMENT_ROOT / "tmp" / f"precheck_{group}"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        world = await _build_kb(group, tmp, children=sample, llm=llm)
        await _verify_index(group, world, expected_children=len(sample))
        calls_before = len(llm.calls)
        rows = await _retrieve_questions(
            world,
            llm,
            question_ids=PREVIEW_QUESTIONS,
            out_jsonl=FIXED_DIR / f"precheck_{group}_queries.jsonl",
        )
        index_calls = llm.calls[group_start_calls:calls_before]
        index_stats[group] = {
            "chunks": len(sample),
            "total_tokens": sum(c["total_tokens"] for c in index_calls),
            "calls": len(index_calls),
            "model_set": sorted({c["actual_model"] for c in index_calls}),
        }
        query_stats[group] = {
            "questions": len(rows),
            "total_tokens": sum(r["total_tokens"] for r in rows),
        }
        # exact cleanup
        from industrial_rag.config import Settings
        from industrial_rag.services.qdrant_collection_service import QdrantCollectionService

        svc = QdrantCollectionService(
            Settings(
                api_key="experiment",
                vector_backend="qdrant",
                qdrant_url=QDRANT_TEST_URL,
                qdrant_collection_prefix=world["prefix"],
                qdrant_kb_id=world["kb"].id,
                qdrant_generation=world["kb"].active_vector_generation.generation,
            )
        )
        await svc.delete_generation()
        await world["engine"].dispose()
    estimate = _estimate(cfg, index_stats, query_stats)
    report = {
        "gate": {k: v[:16] for k, v in gate["p0"].items()},
        "index_stats": index_stats,
        "query_stats": query_stats,
        "llm_summary": llm.summary(),
        "estimate": estimate,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(FIXED_DIR / "precheck_report.json", report)
    write_jsonl(FIXED_DIR / "precheck_llm_calls.jsonl", llm.calls)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


async def run_full(group: str) -> dict[str, Any]:
    cfg = load_frozen_config()
    os.environ["LLM_MODEL"] = cfg["llm_model"]
    os.environ["MODEL_FALLBACK_ENABLED"] = "false"
    gate = check_paid_run_gate()
    if not gate["allowed"]:
        raise RuntimeError(
            "paid run gate blocked: "
            + json.dumps(
                {k: v for k, v in gate["checks"].items() if v is False}, ensure_ascii=False
            )
        )
    print(f"[gate] paid run allowed; estimated tokens={gate['estimated_total_tokens']}")
    gate = assert_consistency()
    children = _all_children(group)
    llm = FixedModelLLM(
        model=cfg["llm_model"],
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=cfg["enable_thinking"],
        cache_path=FIXED_DIR / "cache" / f"full_{group}.jsonl",
        config_hash=gate["p0"]["chunk_config_hash"],
    )
    out_dir = FIXED_DIR / ("P0_pymupdf" if group == "0" else "P1_mineru")
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = FIXED_DIR / f"checkpoint_{group}.json"
    checkpoint: dict[str, Any] = {}
    if checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    tmp = EXPERIMENT_ROOT / "tmp" / f"full_{group}"

    if checkpoint.get("index_complete") and (tmp / "exp.db").is_file():
        print(f"[resume] group {group} index checkpoint found; verifying collections")
        from industrial_rag.config import Settings
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from industrial_rag.db.models import KnowledgeBase
        from industrial_rag.db.session import init_db

        _bootstrap_env(tmp, checkpoint["prefix"])
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp / 'exp.db').as_posix()}", connect_args={"check_same_thread": False}
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            kb = (
                await session.execute(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.id == checkpoint["kb_id"])
                    .options(selectinload(KnowledgeBase.active_vector_generation))
                )
            ).scalar_one()
        world = {
            "kb": kb,
            "settings": Settings.from_env(),
            "factory": factory,
            "engine": engine,
            "prefix": checkpoint["prefix"],
            "tmp": tmp,
        }
        index_check = await _verify_index(group, world, expected_children=len(children))
        index_summary = {"resumed": True}
    else:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        world = await _build_kb(group, tmp, children=children, llm=llm)
        index_check = await _verify_index(group, world, expected_children=len(children))
        index_summary = llm.summary()
        checkpoint.update(
            {
                "group": group,
                "kb_id": world["kb"].id,
                "generation": world["kb"].active_vector_generation.generation,
                "prefix": world["prefix"],
                "index_complete": True,
                "children_count": len(children),
                "llm_calls_at_index": len(llm.calls),
            }
        )
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    if checkpoint.get("queries_complete") and (out_dir / "results.jsonl").is_file():
        print(f"[resume] group {group} queries checkpoint found")
        rows = read_jsonl(out_dir / "results.jsonl")
    else:
        rows = await _retrieve_questions(
            world,
            llm,
            out_jsonl=FIXED_DIR / f"full_{group}_raw_results.jsonl",
        )
        for row in rows:
            row["category"] = QUESTION_CATEGORIES.get(row["question_id"], "未分类")
        write_jsonl(out_dir / "results.jsonl", rows)
        write_jsonl(FIXED_DIR / f"full_{group}_llm_calls.jsonl", llm.calls)
        checkpoint["queries_complete"] = True
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    gold = load_gold()
    mapping = build_evidence_mapping(children)
    write_json(FIXED_DIR / "comparison" / f"evidence_mapping_p{group}.json", mapping)
    retrieval = retrieval_metrics(rows, gold=gold, mapping=mapping)
    citations = citation_metrics(rows, gold=gold)
    categories = category_breakdown(rows, retrieval, gold=gold, mapping=mapping)
    metrics = {
        "group": group,
        "gate": {k: v[:16] for k, v in gate["p0"].items()},
        "index": {"check": index_check, "llm": index_summary, "chunks": len(children)},
        "retrieval": retrieval,
        "citations": citations,
        "categories": categories,
        "llm": llm.summary(),
    }
    write_json(out_dir / "metrics.json", metrics)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--readiness", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--group", choices=["0", "1"])
    args = parser.parse_args()
    if args.precheck:
        report = asyncio.run(run_precheck())
        print("precheck decision:", report["estimate"]["decision"])
        return 0 if report["estimate"]["decision"] == "proceed" else 1
    if args.readiness:
        result = check_paid_run_gate()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["allowed"] else 1
    if args.full:
        metrics = asyncio.run(run_full(args.group))
        print(json.dumps(metrics["retrieval"], ensure_ascii=False, indent=2))
        return 0
    parser.error("choose --precheck or --full")


if __name__ == "__main__":
    sys.exit(main())
