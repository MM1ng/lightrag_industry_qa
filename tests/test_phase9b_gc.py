"""Persistent exact-name GC plan, approval, execution, and resume tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.services.generation_gc_service import GenerationGCService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _GCQdrant:
    def __init__(self, names: set[str]) -> None:
        self.names = set(names)
        self.deleted: list[str] = []
        self.fail_once: set[str] = set()
        self.revision = "original"

    async def collection_exists(self, name: str) -> bool:
        return name in self.names

    async def delete_collection(self, *, collection_name: str) -> None:
        if collection_name in self.fail_once:
            self.fail_once.remove(collection_name)
            raise RuntimeError("injected exact delete failure")
        self.names.discard(collection_name)
        self.deleted.append(collection_name)

    async def scroll(self, *, collection_name: str, **_kwargs):
        return [
            SimpleNamespace(
                id=f"{collection_name}-point",
                payload={"revision": self.revision},
                vector=[0.1, 0.2],
            )
        ], None

    async def close(self) -> None:
        return None


@pytest_asyncio.fixture
async def gc_state(tmp_path, monkeypatch):
    data_root = tmp_path / "kb-data"
    monkeypatch.setenv("KB_DATA_ROOT", str(data_root))
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'gc.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kb_id = "a" * 32
    ids = {name: char * 32 for name, char in zip(
        ("active", "rollback", "archived", "candidate", "audit"),
        "bcdef",
        strict=True,
    )}
    now = datetime.now(UTC)
    workspaces = {}
    async with factory() as session:
        for name in ids:
            workspace = data_root / kb_id / "qdrant" / "generations" / name / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "evidence.json").write_text(name, encoding="utf-8")
            workspaces[name] = workspace
        session.add(
            KnowledgeBase(
                id=kb_id,
                name="gc",
                status="ready",
                workspace_path=str(workspaces["active"]),
                upload_path=str(data_root / kb_id / "uploads"),
                parsed_path=str(data_root / kb_id / "parsed"),
                vector_backend="qdrant",
                active_vector_generation_id=ids["active"],
                last_rollback_target_generation_id=ids["rollback"],
            )
        )
        status_by_name = {
            "active": VectorIndexGenerationStatus.active,
            "rollback": VectorIndexGenerationStatus.archived,
            "archived": VectorIndexGenerationStatus.archived,
            "candidate": VectorIndexGenerationStatus.failed,
            "audit": VectorIndexGenerationStatus.failed,
        }
        for name, generation_id in ids.items():
            session.add(
                VectorIndexGeneration(
                    id=generation_id,
                    knowledge_base_id=kb_id,
                    backend="qdrant",
                    generation=f"g{name}00000000",
                    status=status_by_name[name],
                    workspace_path=str(workspaces[name]),
                    collections={"chunks": f"exact_{name}_chunks"},
                    document_manifest_hash="1" * 64,
                    child_chunks_manifest_hash="2" * 64,
                    embedding_config_hash="3" * 64,
                    chunking_config_hash="4" * 64,
                    audit_frozen=name == "audit",
                    created_at=now - timedelta(days=30 if name != "active" else 1),
                )
            )
        await session.commit()
    qdrant = _GCQdrant({f"exact_{name}_chunks" for name in ids})
    settings = Settings(
        api_key="provider-test-key",
        vector_backend="qdrant",
        qdrant_url="http://qdrant.invalid",
        working_dir=tmp_path,
    )
    yield factory, settings, qdrant, kb_id, ids, workspaces
    await engine.dispose()


@pytest.mark.asyncio
async def test_gc_plan_protects_active_rollback_and_audit_and_deletes_exact_names(gc_state) -> None:
    factory, settings, qdrant, kb_id, ids, workspaces = gc_state
    async with factory() as session:
        service = GenerationGCService(
            session,
            settings=settings,
            qdrant_client_factory=lambda: qdrant,
        )
        plan = await service.plan(
            kb_id,
            actor="admin:aaaaaaaaaaaa",
            failed_retention_days=0,
            archived_keep_count=0,
        )
        planned_ids = {item["generation_id"] for item in plan["items"]}
        assert ids["candidate"] in planned_ids
        assert ids["archived"] in planned_ids
        assert ids["active"] not in planned_ids
        assert ids["rollback"] not in planned_ids
        assert ids["audit"] not in planned_ids
        result = await service.execute(
            kb_id,
            plan["plan_id"],
            manifest_hash=plan["manifest_hash"],
            actor="admin:bbbbbbbbbbbb",
        )
        kb = await session.get(KnowledgeBase, kb_id)
    assert result["status"] == "completed"
    assert result["approved_by"] == "admin:bbbbbbbbbbbb"
    assert kb.active_vector_generation_id == ids["active"]
    assert set(qdrant.deleted) == {"exact_candidate_chunks", "exact_archived_chunks"}
    assert not workspaces["candidate"].exists()
    assert not workspaces["archived"].exists()
    assert workspaces["active"].exists()
    assert workspaces["rollback"].exists()
    assert workspaces["audit"].exists()


@pytest.mark.asyncio
async def test_partial_gc_plan_resumes_same_manifest(gc_state) -> None:
    factory, settings, qdrant, kb_id, _ids, _workspaces = gc_state
    qdrant.fail_once.add("exact_candidate_chunks")
    async with factory() as session:
        service = GenerationGCService(
            session,
            settings=settings,
            qdrant_client_factory=lambda: qdrant,
        )
        plan = await service.plan(
            kb_id,
            actor="admin:aaaaaaaaaaaa",
            failed_retention_days=0,
            archived_keep_count=0,
        )
        first = await service.execute(
            kb_id,
            plan["plan_id"],
            manifest_hash=plan["manifest_hash"],
            actor="admin:bbbbbbbbbbbb",
        )
        assert first["status"] == "partial_failed"
        second = await service.execute(
            kb_id,
            plan["plan_id"],
            manifest_hash=plan["manifest_hash"],
            actor="admin:bbbbbbbbbbbb",
        )
    assert second["status"] == "completed"


@pytest.mark.asyncio
async def test_gc_plan_rejects_qdrant_content_changed_after_plan(gc_state) -> None:
    factory, settings, qdrant, kb_id, _ids, workspaces = gc_state
    async with factory() as session:
        service = GenerationGCService(
            session,
            settings=settings,
            qdrant_client_factory=lambda: qdrant,
        )
        plan = await service.plan(
            kb_id,
            actor="admin:aaaaaaaaaaaa",
            failed_retention_days=0,
            archived_keep_count=0,
        )
        qdrant.revision = "mutated-after-plan"
        result = await service.execute(
            kb_id,
            plan["plan_id"],
            manifest_hash=plan["manifest_hash"],
            actor="admin:bbbbbbbbbbbb",
        )
    assert result["status"] == "partial_failed"
    assert qdrant.deleted == []
    assert workspaces["candidate"].exists()
    assert workspaces["archived"].exists()
    assert all(
        item["error"] == "generation content changed"
        for item in result["result"]["items"]
    )
