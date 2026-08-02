"""Behavioral persistence tests for validation and GC records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from industrial_rag.db.models import (
    Base,
    GCPlanStatus,
    KnowledgeBase,
    ValidationRunStatus,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _seed(session):
    kb = KnowledgeBase(
        id="a" * 32,
        name="phase9b",
        workspace_path="C:/tmp/kb",
        upload_path="C:/tmp/uploads",
        parsed_path="C:/tmp/parsed",
    )
    generation = VectorIndexGeneration(
        id="b" * 32,
        knowledge_base_id=kb.id,
        backend="qdrant",
        generation="g-phase9b",
        status=VectorIndexGenerationStatus.ready,
        workspace_path="C:/tmp/generation",
        collections={"chunks": "exact_chunks"},
        document_manifest_hash="1" * 64,
        child_chunks_manifest_hash="2" * 64,
        embedding_config_hash="3" * 64,
        chunking_config_hash="4" * 64,
    )
    session.add_all([kb, generation])
    await session.flush()
    return kb, generation


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_validation_repository_returns_only_current_unexpired_pass(session) -> None:
    from industrial_rag.repositories.validation_run_repository import (
        ValidationRunRepository,
    )

    kb, generation = await _seed(session)
    now = datetime.now(tz=UTC)
    repository = ValidationRunRepository(session)
    run = await repository.create(
        knowledge_base_id=kb.id,
        generation_id=generation.id,
        golden_set_version="phase9b-canonical-20-v1",
        golden_set_sha256="5" * 64,
        runner_version="official-fastapi-v1",
        app_git_commit="6" * 40,
        configured_model="qwen-plus-2025-07-28",
        strategy_fingerprint="7" * 64,
        generation_manifest_hash="8" * 64,
        qdrant_content_fingerprint="9" * 64,
        document_registry_fingerprint="a" * 64,
        generation_content_epoch=0,
        actor="admin:123456789abc",
        expires_at=now + timedelta(hours=1),
    )
    await repository.finalize(
        run.id,
        passed=True,
        metrics={"answer_citation_accuracy": 0.8333},
        artifact_path="C:/artifacts/run.jsonl",
        artifact_sha256="b" * 64,
        finished_at=now,
    )

    eligible = await repository.latest_eligible(
        generation.id,
        golden_set_version="phase9b-canonical-20-v1",
        golden_set_sha256="5" * 64,
        now=now,
    )
    assert eligible is not None
    assert eligible.id == run.id
    assert eligible.status is ValidationRunStatus.passed
    assert eligible.passed is True

    assert (
        await repository.latest_eligible(
            generation.id,
            golden_set_version="phase9b-canonical-20-v2",
            golden_set_sha256="5" * 64,
            now=now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_gc_plan_repository_requires_explicit_admin_approval(session) -> None:
    from industrial_rag.repositories.gc_plan_repository import GCPlanRepository

    kb, _generation = await _seed(session)
    now = datetime.now(tz=UTC)
    repository = GCPlanRepository(session)
    plan = await repository.create(
        knowledge_base_id=kb.id,
        policy={"failed_retention_days": 7, "archived_keep_count": 3},
        items=[{"generation_id": "c" * 32, "collections": ["exact_chunks"]}],
        manifest_hash="d" * 64,
        created_by="admin:123456789abc",
        expires_at=now + timedelta(minutes=30),
    )

    assert plan.status is GCPlanStatus.planned
    approved = await repository.approve(
        plan.id, approved_by="admin:abcdef123456", now=now
    )
    assert approved is not None
    assert approved.status is GCPlanStatus.approved
    assert approved.approved_by == "admin:abcdef123456"
