"""Persistent Update Job claim, checkpoint, and recovery behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from industrial_rag.db.models import (
    Base,
    KnowledgeBase,
    UpdateJob,
    UpdateJobStatus,
    UpdateOperation,
)
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def job_factory(tmp_path):
    database_path = tmp_path / "jobs.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        kb = KnowledgeBase(
            id="a" * 32,
            name="phase9b-jobs",
            workspace_path="C:/tmp/jobs",
            upload_path="C:/tmp/jobs/uploads",
            parsed_path="C:/tmp/jobs/parsed",
        )
        session.add(kb)
        session.add(
            UpdateJob(
                id="b" * 32,
                knowledge_base_id=kb.id,
                operation=UpdateOperation.add,
                status=UpdateJobStatus.pending,
                created_by="admin:123456789abc",
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_workers_atomically_claim_one_job_once(job_factory) -> None:
    now = datetime.now(tz=UTC)

    async def claim(worker: str, token: str, fence: int):
        async with job_factory() as session:
            return await UpdateJobRepository(session).claim_next(
                worker_id=worker,
                lease_token=token,
                fencing_token=fence,
                now=now,
                lease_expires_at=now + timedelta(seconds=30),
            )

    first, second = await asyncio.gather(
        claim("worker-a", "token-a", 1), claim("worker-b", "token-b", 2)
    )
    claimed = [job for job in (first, second) if job is not None]
    assert len(claimed) == 1
    assert claimed[0].id == "b" * 32
    assert claimed[0].status is UpdateJobStatus.claimed
    assert claimed[0].attempt == 1


@pytest.mark.asyncio
async def test_stale_worker_cannot_checkpoint_or_finish_job(job_factory) -> None:
    now = datetime.now(tz=UTC)
    async with job_factory() as session:
        repository = UpdateJobRepository(session)
        job = await repository.claim_next(
            worker_id="worker-a",
            lease_token="token-a",
            fencing_token=10,
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        assert job is not None

    async with job_factory() as session:
        repository = UpdateJobRepository(session)
        assert (
            await repository.save_checkpoint(
                job.id,
                worker_id="worker-old",
                lease_token="token-old",
                fencing_token=9,
                checkpoint={"stage": "qdrant_written"},
                status=UpdateJobStatus.running,
                now=now,
            )
            is False
        )
        assert (
            await repository.mark_succeeded_fenced(
                job.id,
                worker_id="worker-old",
                lease_token="token-old",
                fencing_token=9,
                result={"candidate_generation_id": "c" * 32},
                now=now,
            )
            is False
        )
        current = await repository.get(job.id)
        assert current is not None
        assert current.status is UpdateJobStatus.claimed


@pytest.mark.asyncio
async def test_expired_job_becomes_recoverable_and_success_is_not_reclaimed(job_factory) -> None:
    started = datetime.now(tz=UTC)
    async with job_factory() as session:
        repository = UpdateJobRepository(session)
        job = await repository.claim_next(
            worker_id="worker-a",
            lease_token="token-a",
            fencing_token=1,
            now=started,
            lease_expires_at=started + timedelta(seconds=1),
        )
        assert job is not None
        assert await repository.save_checkpoint(
            job.id,
            worker_id="worker-a",
            lease_token="token-a",
            fencing_token=1,
            checkpoint={"stage": "parsed", "document_id": "d" * 32},
            status=UpdateJobStatus.building,
            now=started,
        )

    after_expiry = started + timedelta(seconds=2)
    async with job_factory() as session:
        repository = UpdateJobRepository(session)
        recovered_ids = await repository.mark_expired_for_recovery(now=after_expiry)
        assert recovered_ids == [job.id]
        recovered = await repository.claim_next(
            worker_id="worker-b",
            lease_token="token-b",
            fencing_token=2,
            now=after_expiry,
            lease_expires_at=after_expiry + timedelta(seconds=30),
        )
        assert recovered is not None
        assert recovered.id == job.id
        assert recovered.attempt == 2
        assert recovered.checkpoint == {"stage": "parsed", "document_id": "d" * 32}
        assert await repository.mark_succeeded_fenced(
            recovered.id,
            worker_id="worker-b",
            lease_token="token-b",
            fencing_token=2,
            result={"candidate_generation_id": "c" * 32},
            now=after_expiry,
        )

    async with job_factory() as session:
        repository = UpdateJobRepository(session)
        assert (
            await repository.claim_next(
                worker_id="worker-c",
                lease_token="token-c",
                fencing_token=3,
                now=after_expiry,
                lease_expires_at=after_expiry + timedelta(seconds=30),
            )
            is None
        )


@pytest.mark.asyncio
async def test_worker_claim_binds_job_to_matching_kb_lease(job_factory) -> None:
    from industrial_rag.services.update_job_worker import UpdateJobWorker

    now = datetime.now(tz=UTC)
    worker = UpdateJobWorker(
        job_factory,
        worker_id="phase9b-worker",
        lease_ttl=timedelta(seconds=30),
    )
    claimed = await worker.claim_one(now=now)

    assert claimed is not None
    assert claimed.job_id == "b" * 32
    assert claimed.knowledge_base_id == "a" * 32
    assert claimed.lease.owner == "phase9b-worker"
    assert claimed.fencing_token == claimed.lease.fencing_token

    assert await worker.heartbeat(claimed, now=now + timedelta(seconds=1))
    assert await worker.complete(
        claimed,
        result={"candidate_generation_id": "c" * 32},
        now=now + timedelta(seconds=2),
    )
