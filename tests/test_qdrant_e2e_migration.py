"""Opt-in Phase 3V E2E: Nano -> Qdrant migration, rollback, staleness, restart.

Gated by IRA_QDRANT_E2E=1 (implies a running Qdrant at QDRANT_TEST_URL and
real DashScope credentials in the environment). Uses one small real PDF and the
real LightRAG pipeline (real text-embedding-v4 embeddings + real LLM entity
extraction). All Qdrant collections are created under a random prefix and
precisely deleted at the end of the module.

Run:
    $env:IRA_QDRANT_E2E="1"
    python -m pytest tests/test_qdrant_e2e_migration.py -q -s
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path

import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    KnowledgeBase,
    LifecycleTask,
    TaskStatus,
    TaskType,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import init_db, reset_for_testing
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.services.parse_service import load_child_chunks
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService
from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager
from industrial_rag.services.task_context import TaskExecutionContext
from industrial_rag.storage_layout import kb_parsed_dir
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

QDRANT_TEST_URL = os.environ.get("QDRANT_TEST_URL", "http://127.0.0.1:16333")

pytestmark = pytest.mark.skipif(
    not os.environ.get("IRA_QDRANT_E2E"),
    reason="Real DashScope + Qdrant E2E is opt-in via IRA_QDRANT_E2E=1",
)


def _write_test_pdf(path: Path, text: str) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 540, 790), text, fontsize=11, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _delete_prefix_collections(url: str, prefix: str) -> None:
    client = AsyncQdrantClient(url=url, timeout=10)
    try:
        response = await client.get_collections()
        for item in response.collections:
            if item.name.startswith(prefix):
                await client.delete_collection(item.name)
    finally:
        await client.close()


def _run(coro):
    return asyncio.run(coro)


class E2EWorld:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.prefix = f"ira_e2e_{secrets.token_hex(4)}"
        self.qdrant_url = QDRANT_TEST_URL
        self.kb_data = tmp / "kb_data"
        self.kb_data.mkdir(parents=True, exist_ok=True)
        self.db_url = "sqlite+aiosqlite:///" + (tmp / "e2e.db").as_posix()
        self.pdf_path = tmp / "pump.pdf"
        self.settings: Settings | None = None
        self.runtime_manager: KnowledgeBaseRuntimeManager | None = None
        self.factory: async_sessionmaker | None = None
        self.kb_id: str | None = None
        self.doc_id: str | None = None


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> E2EWorld:
    tmp = tmp_path_factory.mktemp("e2e")
    world_obj = E2EWorld(tmp)
    env = {
        "KB_DATA_ROOT": str(tmp / "kb_data"),
        "DATABASE_URL": "sqlite+aiosqlite:///" + (tmp / "e2e.db").as_posix(),
        "QDRANT_URL": QDRANT_TEST_URL,
        "QDRANT_COLLECTION_PREFIX": world_obj.prefix,
    }
    for key, value in env.items():
        os.environ[key] = value
    world_obj.settings = Settings.from_env()
    world_obj.runtime_manager = KnowledgeBaseRuntimeManager()
    reset_for_testing()
    _run(init_db(drop_all=True))
    _run(world_obj.runtime_manager.close_all())
    engine = create_async_engine(world_obj.db_url, connect_args={"check_same_thread": False})
    world_obj.factory = async_sessionmaker(engine, expire_on_commit=False)
    world_obj.engine = engine
    yield world_obj
    # teardown
    _run(world_obj.runtime_manager.close_all())
    _run(_delete_prefix_collections(QDRANT_TEST_URL, world_obj.prefix))
    _run(engine.dispose())
    reset_for_testing()


async def _create_task(
    factory: async_sessionmaker,
    kb_id: str,
    task_type: TaskType,
    *,
    document_id: str | None = None,
) -> LifecycleTask:
    async with factory() as session:
        task_repo = TaskRepository(session)
        task = await task_repo.create(
            knowledge_base_id=kb_id,
            document_id=document_id,
            task_type=task_type,
        )
        await session.commit()
        return task


async def _run_task(
    factory: async_sessionmaker,
    task: LifecycleTask,
    *,
    settings: Settings,
    runtime_manager: KnowledgeBaseRuntimeManager,
) -> dict:
    # Importing handler_impls is required so the handlers self-register.
    import industrial_rag.services.handler_impls  # noqa: F401
    from industrial_rag.services.task_handlers import get_builtin_registry

    registry = get_builtin_registry()
    handler = registry.get(task.task_type)
    assert handler is not None
    async with factory() as session:
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
        result = await handler(ctx)
        await session.commit()
        return {"result": result, "task_id": task.id}


async def _mark_task(factory: async_sessionmaker, task_id: str, *, status: TaskStatus) -> None:
    async with factory() as session:
        task_repo = TaskRepository(session)
        if status is TaskStatus.succeeded:
            await task_repo.mark_succeeded(task_id)
        elif status is TaskStatus.failed:
            await task_repo.mark_failed(task_id, error_code="test")
        await session.commit()


async def _kb(factory: async_sessionmaker, kb_id: str) -> KnowledgeBase:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with factory() as session:
        result = await session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .options(selectinload(KnowledgeBase.active_vector_generation))
        )
        kb = result.scalar_one_or_none()
        assert kb is not None
        return kb


async def _collections_for(url: str, prefix: str) -> list[str]:
    client = AsyncQdrantClient(url=url, timeout=10)
    try:
        response = await client.get_collections()
        return [item.name for item in response.collections if item.name.startswith(prefix)]
    finally:
        await client.close()


def _make_client(world: E2EWorld) -> AsyncClient:
    from industrial_rag.api import create_app

    app = create_app(settings=world.settings)
    app.state.service_api_key = None
    app.state.runtime = None
    app.state.resolved_settings = world.settings
    app.state.runtime_manager = world.runtime_manager
    app.state.task_executor = None
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _setup_kb(world: E2EWorld) -> None:
    """Create KB + document + run real PyMuPDF parse via handle_parse."""
    pdf_text = (
        "离心泵启动前需要检查阀门状态和润滑油位。\n\n"
        "警告：启动前必须确认所有安全装置已就位，禁止带负荷启动。\n\n"
        "日常维护时每运行 500 小时应更换润滑油，并检查机械密封泄漏情况。"
    )
    file_hash = _write_test_pdf(world.pdf_path, pdf_text)
    async with _make_client(world) as client:
        response = await client.post("/v1/knowledge-bases", json={"name": "Phase3V-E2E"})
        assert response.status_code == 201, response.text
        world.kb_id = response.json()["id"]

    async with world.factory() as session:
        doc_repo = DocumentRepository(session)
        doc = await doc_repo.create(
            knowledge_base_id=world.kb_id,
            original_file_name="pump.pdf",
            stored_file_name="pump.pdf",
            file_path=str(world.pdf_path),
            file_hash=file_hash,
            file_size=world.pdf_path.stat().st_size,
            mime_type="application/pdf",
        )
        world.doc_id = doc.id
        await session.commit()

    task = await _create_task(
        world.factory, world.kb_id, TaskType.parse, document_id=world.doc_id
    )
    outcome = await _run_task(
        world.factory,
        task,
        settings=world.settings,
        runtime_manager=world.runtime_manager,
    )
    assert outcome["result"].success, (
        f"parse failed: {outcome['result'].error_code}: {outcome['result'].error_message}"
    )
    await _mark_task(world.factory, outcome["task_id"], status=TaskStatus.succeeded)
    follow_up_id = outcome["result"].result["follow_up_task_id"]
    async with world.factory() as session:
        task_repo = TaskRepository(session)
        follow_up = await task_repo.get(follow_up_id)
    assert follow_up is not None
    return follow_up


async def _build_nano(world: E2EWorld, follow_up: LifecycleTask) -> None:
    outcome = await _run_task(
        world.factory,
        follow_up,
        settings=world.settings,
        runtime_manager=world.runtime_manager,
    )
    assert outcome["result"].success, (
        f"nano build failed: {outcome['result'].error_code}: {outcome['result'].error_message}"
    )
    await _mark_task(world.factory, outcome["task_id"], status=TaskStatus.succeeded)
    kb = await _kb(world.factory, world.kb_id)
    assert kb.vector_backend == "nano"
    assert kb.status.value == "ready"


# ---------------------------------------------------------------------------
# Main E2E scenario
# ---------------------------------------------------------------------------


def test_phase3_e2e_nano_qdrant_migration_rollback_restart(world: E2EWorld) -> None:
    async def scenario() -> None:
        # 1. parse + nano baseline
        follow_up = await _setup_kb(world)
        await _build_nano(world, follow_up)
        kb = await _kb(world.factory, world.kb_id)
        nano_generation = kb.active_vector_generation
        assert nano_generation is not None and nano_generation.backend == "nano"
        from industrial_rag.storage_layout import kb_nano_workspace

        assert kb_nano_workspace(world.kb_id).is_dir()

        # child chunks used for fingerprinting
        async with world.factory() as session:
            doc = await DocumentRepository(session).get(world.doc_id)
        parsed_dir = kb_parsed_dir(world.kb_id) / "documents" / doc.id
        children = load_child_chunks(parsed_dir)
        assert children, "no child chunks produced by parse"

        # 2. Nano -> Qdrant via API
        async with _make_client(world) as client:
            first = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/vector-backend",
                json={"target_backend": "qdrant"},
            )
            second = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/vector-backend",
                json={"target_backend": "qdrant"},
            )
            conflict = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/vector-backend",
                json={"target_backend": "nano"},
            )
        assert first.status_code == 202, first.text
        assert second.status_code == 202
        assert second.json()["task_id"] == first.json()["task_id"]
        assert conflict.status_code == 409  # active qdrant task pending

        migrate_task = await _kb_task(world.factory, first.json()["task_id"])
        outcome = await _run_task(
            world.factory,
            migrate_task,
            settings=world.settings,
            runtime_manager=world.runtime_manager,
        )
        assert outcome["result"].success, (
            f"migration failed: {outcome['result'].error_code}: {outcome['result'].error_message}"
        )
        await _mark_task(world.factory, outcome["task_id"], status=TaskStatus.succeeded)

        kb = await _kb(world.factory, world.kb_id)
        assert kb.vector_backend == "qdrant"
        assert kb.active_vector_generation is not None
        assert kb.active_vector_generation.backend == "qdrant"
        assert kb.active_vector_generation.generation.startswith("g")
        assert kb.status.value == "ready"

        # nano workspace preserved + runtime evicted
        assert kb_nano_workspace(world.kb_id).is_dir()
        assert not world.runtime_manager.is_cached(world.kb_id)

        # 3. real Qdrant verify: collection count matches child chunks
        service = QdrantCollectionService(
            Settings(
                api_key="e2e",
                vector_backend="qdrant",
                qdrant_url=world.qdrant_url,
                qdrant_kb_id=world.kb_id,
                qdrant_generation=kb.active_vector_generation.generation,
                qdrant_collection_prefix=world.prefix,
            )
        )
        chunk_count = await service.verify_generation(expected_chunks=len(children))
        assert chunk_count >= len(children)

        # 4. real query through the API (Qdrant runtime)
        async with _make_client(world) as client:
            query_response = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/query",
                json={"query": "启动离心泵前需要检查什么？"},
            )
        assert query_response.status_code == 200, query_response.text
        body = query_response.json()
        assert body["status"] in {"success", "insufficient_evidence"}
        assert world.runtime_manager.is_cached(world.kb_id)

        # 5. simulate service restart: evict runtime, query again
        await world.runtime_manager.close_all()
        assert not world.runtime_manager.is_cached(world.kb_id)
        async with _make_client(world) as client:
            after_restart = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/query",
                json={"query": "润滑油应该多久更换一次？"},
            )
        assert after_restart.status_code == 200, after_restart.text
        assert after_restart.json()["status"] in {"success", "insufficient_evidence"}

        # 6. stale rollback rejected: append a valid-but-different child chunk
        #    (a malformed line would make load_child_chunks raise -> wrong error code)
        child_file = parsed_dir / "current" / "child_chunks.jsonl"
        original = child_file.read_text(encoding="utf-8")
        drift = json.loads(original.strip().splitlines()[0])
        drift["chunk_id"] = f"{drift['chunk_id']}_drift"
        drift["content"] = f"{drift['content']} DRIFT"
        drift["embedding_content"] = f"{drift['embedding_content']} DRIFT"
        with child_file.open("a", encoding="utf-8") as handle:
            handle.write("\n" + json.dumps(drift, ensure_ascii=False) + "\n")
        async with _make_client(world) as client:
            stale = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/vector-backend",
                json={"target_backend": "nano"},
            )
        assert stale.status_code == 202, stale.text
        stale_task = await _kb_task(world.factory, stale.json()["task_id"])
        stale_outcome = await _run_task(
            world.factory,
            stale_task,
            settings=world.settings,
            runtime_manager=world.runtime_manager,
        )
        assert stale_outcome["result"].success is False
        assert stale_outcome["result"].error_code == "nano_generation_stale"
        await _mark_task(world.factory, stale_outcome["task_id"], status=TaskStatus.failed)
        kb = await _kb(world.factory, world.kb_id)
        assert kb.vector_backend == "qdrant", "stale rollback must not switch backend"

        # 7. restore fingerprint -> rollback succeeds
        child_file.write_text(original, encoding="utf-8")
        async with _make_client(world) as client:
            rollback = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/vector-backend",
                json={"target_backend": "nano"},
            )
        assert rollback.status_code == 202, rollback.text
        rollback_task = await _kb_task(world.factory, rollback.json()["task_id"])
        rollback_outcome = await _run_task(
            world.factory,
            rollback_task,
            settings=world.settings,
            runtime_manager=world.runtime_manager,
        )
        assert rollback_outcome["result"].success, (
            f"rollback failed: {rollback_outcome['result'].error_code}: "
            f"{rollback_outcome['result'].error_message}"
        )
        await _mark_task(world.factory, rollback_outcome["task_id"], status=TaskStatus.succeeded)
        kb = await _kb(world.factory, world.kb_id)
        assert kb.vector_backend == "nano"
        assert kb.active_vector_generation is not None
        assert kb.active_vector_generation.backend == "nano"

        # 8. query works after rollback (nano runtime rebuilt)
        async with _make_client(world) as client:
            nano_query = await client.post(
                f"/v1/knowledge-bases/{world.kb_id}/query",
                json={"query": "机械密封泄漏如何检查？"},
            )
        assert nano_query.status_code == 200, nano_query.text
        assert nano_query.json()["status"] in {"success", "insufficient_evidence"}

    _run(scenario())


async def _kb_task(factory: async_sessionmaker, task_id: str) -> LifecycleTask:
    async with factory() as session:
        task = await TaskRepository(session).get(task_id)
        assert task is not None
        return task


def test_phase3_e2e_migration_failure_does_not_promote(world: E2EWorld) -> None:
    """Qdrant unreachable during migration -> task fails, KB stays Nano."""
    async def scenario() -> None:
        # fresh small KB + parse (no nano build needed)
        pdf_text = "轴封泄漏应立即停机并联系维修人员处理。"
        file_hash = _write_test_pdf(world.tmp / "pump2.pdf", pdf_text)
        async with _make_client(world) as client:
            response = await client.post("/v1/knowledge-bases", json={"name": "FailKB"})
            assert response.status_code == 201, response.text
            kb2_id = response.json()["id"]
        async with world.factory() as session:
            doc = await DocumentRepository(session).create(
                knowledge_base_id=kb2_id,
                original_file_name="pump2.pdf",
                stored_file_name="pump2.pdf",
                file_path=str(world.tmp / "pump2.pdf"),
                file_hash=file_hash,
                file_size=(world.tmp / "pump2.pdf").stat().st_size,
                mime_type="application/pdf",
            )
            await session.commit()
        parse_task = await _create_task(
            world.factory, kb2_id, TaskType.parse, document_id=doc.id
        )
        parse_outcome = await _run_task(
            world.factory,
            parse_task,
            settings=world.settings,
            runtime_manager=world.runtime_manager,
        )
        assert parse_outcome["result"].success

        # migrate with an unreachable Qdrant URL
        dead_settings = Settings(
            api_key=world.settings.api_key,
            vector_backend="qdrant",
            qdrant_url="http://127.0.0.1:1",
            qdrant_collection_prefix=world.prefix,
        )
        migrate_task = await _create_task(world.factory, kb2_id, TaskType.migrate_to_qdrant)
        outcome = await _run_task(
            world.factory,
            migrate_task,
            settings=dead_settings,
            runtime_manager=world.runtime_manager,
        )
        assert outcome["result"].success is False
        await _mark_task(world.factory, outcome["task_id"], status=TaskStatus.failed)

        kb2 = await _kb(world.factory, kb2_id)
        assert kb2.vector_backend == "nano", "failed migration must not promote backend"
        async with world.factory() as session:
            generations = await VectorIndexGenerationRepository(session).list_for_kb(kb2_id)
        active_qdrant = [
            g
            for g in generations
            if g.backend == "qdrant" and g.status == VectorIndexGenerationStatus.active
        ]
        assert active_qdrant == [], "failed migration left an active Qdrant generation"
        # no orphan collections under the KB's qdrant shadow
        names = await _collections_for(world.qdrant_url, world.prefix)
        kb2_collections = [name for name in names if f"kb_{kb2_id}_" in name]
        assert kb2_collections == [], f"orphan collections: {kb2_collections}"

    _run(scenario())
