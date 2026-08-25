"""Cross-session lease and fencing behavior for Phase 9B KB writers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from industrial_rag.db.models import (
    Base,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def lease_factory(tmp_path):
    database_path = tmp_path / "leases.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for kb_id in ("a" * 32, "b" * 32):
            session.add(
                KnowledgeBase(
                    id=kb_id,
                    name=kb_id[:1],
                    workspace_path=f"C:/tmp/{kb_id}",
                    upload_path=f"C:/tmp/{kb_id}/uploads",
                    parsed_path=f"C:/tmp/{kb_id}/parsed",
                )
            )
        session.add(
            VectorIndexGeneration(
                id="c" * 32,
                knowledge_base_id="a" * 32,
                backend="qdrant",
                generation="g-lease",
                status=VectorIndexGenerationStatus.building,
                workspace_path="C:/tmp/g-lease",
                collections={"chunks": "exact_chunks"},
                document_manifest_hash="1" * 64,
                child_chunks_manifest_hash="2" * 64,
                embedding_config_hash="3" * 64,
                chunking_config_hash="4" * 64,
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_sessions_competing_for_one_kb_yield_one_lease(lease_factory) -> None:
    from industrial_rag.services.kb_lease_service import KBLeaseService

    now = datetime.now(tz=UTC)

    async def acquire(owner: str):
        async with lease_factory() as session:
            return await KBLeaseService(session).acquire(
                "a" * 32,
                owner=owner,
                operation="update",
                now=now,
                ttl=timedelta(seconds=30),
            )

    first, second = await asyncio.gather(acquire("worker-a"), acquire("worker-b"))
    assert sum(handle is not None for handle in (first, second)) == 1

    async with lease_factory() as session:
        other = await KBLeaseService(session).acquire(
            "b" * 32,
            owner="worker-c",
            operation="update",
            now=now,
            ttl=timedelta(seconds=30),
        )
    assert other is not None


@pytest.mark.asyncio
async def test_expiry_increments_fencing_and_rejects_old_worker_write(lease_factory) -> None:
    from industrial_rag.services.kb_lease_service import KBLeaseService

    started = datetime.now(tz=UTC)
    async with lease_factory() as session:
        first_service = KBLeaseService(session)
        first = await first_service.acquire(
            "a" * 32,
            owner="old-worker",
            operation="build",
            now=started,
            ttl=timedelta(seconds=1),
        )
    assert first is not None

    after_expiry = started + timedelta(seconds=2)
    async with lease_factory() as session:
        second_service = KBLeaseService(session)
        second = await second_service.acquire(
            "a" * 32,
            owner="new-worker",
            operation="build",
            now=after_expiry,
            ttl=timedelta(seconds=30),
        )
    assert second is not None
    assert second.fencing_token > first.fencing_token

    async with lease_factory() as session:
        stale_service = KBLeaseService(session)
        stale_changed = await stale_service.update_generation_status(
            "c" * 32,
            first,
            VectorIndexGenerationStatus.failed,
            now=after_expiry,
        )
        current_changed = await stale_service.update_generation_status(
            "c" * 32,
            second,
            VectorIndexGenerationStatus.ready,
            now=after_expiry,
        )
        generation = await session.get(VectorIndexGeneration, "c" * 32)

    assert stale_changed is False
    assert current_changed is True
    assert generation is not None
    assert generation.status is VectorIndexGenerationStatus.ready


@pytest.mark.asyncio
async def test_release_requires_owner_and_token_and_is_idempotent(lease_factory) -> None:
    from industrial_rag.services.kb_lease_service import KBLeaseService, LeaseHandle

    now = datetime.now(tz=UTC)
    async with lease_factory() as session:
        service = KBLeaseService(session)
        handle = await service.acquire(
            "a" * 32,
            owner="owner-a",
            operation="promote",
            now=now,
            ttl=timedelta(seconds=30),
        )
        assert handle is not None
        forged = LeaseHandle(
            kb_id=handle.kb_id,
            owner="owner-b",
            lease_token=handle.lease_token,
            fencing_token=handle.fencing_token,
            expires_at=handle.expires_at,
        )
        assert await service.release(forged) is False
        assert await service.release(handle) is True
        assert await service.release(handle) is True
