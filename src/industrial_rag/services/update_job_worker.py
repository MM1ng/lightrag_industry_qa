"""Persistent Update Job claims bound to durable per-KB fencing leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.services.kb_lease_service import KBLeaseService, LeaseHandle


@dataclass(frozen=True, slots=True)
class ClaimedUpdateJob:
    job_id: str
    knowledge_base_id: str
    lease: LeaseHandle
    attempt: int
    checkpoint: dict | None

    @property
    def fencing_token(self) -> int:
        return self.lease.fencing_token


class UpdateJobWorker:
    """Claims jobs atomically and couples every claim to the KB writer lease."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str,
        lease_ttl: timedelta = timedelta(seconds=60),
    ) -> None:
        self._session_factory = session_factory
        self.worker_id = worker_id
        self.lease_ttl = lease_ttl

    async def claim_one(self, *, now: datetime) -> ClaimedUpdateJob | None:
        async with self._session_factory() as session:
            candidate = await UpdateJobRepository(session).next_eligible()
            if candidate is None:
                return None
            job_id = candidate.id
            kb_id = candidate.knowledge_base_id

        async with self._session_factory() as session:
            lease = await KBLeaseService(session).acquire(
                kb_id,
                owner=self.worker_id,
                operation="update_job",
                job_id=job_id,
                now=now,
                ttl=self.lease_ttl,
            )
        if lease is None:
            return None

        async with self._session_factory() as session:
            job = await UpdateJobRepository(session).claim_specific(
                job_id,
                worker_id=self.worker_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                now=now,
                lease_expires_at=now + self.lease_ttl,
            )
        if job is None:
            async with self._session_factory() as session:
                await KBLeaseService(session).release(lease)
            return None
        return ClaimedUpdateJob(
            job_id=job.id,
            knowledge_base_id=job.knowledge_base_id,
            lease=lease,
            attempt=job.attempt,
            checkpoint=job.checkpoint,
        )

    async def heartbeat(self, claimed: ClaimedUpdateJob, *, now: datetime) -> bool:
        async with self._session_factory() as session:
            lease_ok = await KBLeaseService(session).heartbeat(
                claimed.lease,
                now=now,
                ttl=self.lease_ttl,
            )
        if not lease_ok:
            return False
        async with self._session_factory() as session:
            return await UpdateJobRepository(session).heartbeat_claim(
                claimed.job_id,
                worker_id=self.worker_id,
                lease_token=claimed.lease.lease_token,
                fencing_token=claimed.fencing_token,
                now=now,
                lease_expires_at=now + self.lease_ttl,
            )

    async def complete(
        self,
        claimed: ClaimedUpdateJob,
        *,
        result: dict,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            completed = await UpdateJobRepository(session).mark_succeeded_fenced(
                claimed.job_id,
                worker_id=self.worker_id,
                lease_token=claimed.lease.lease_token,
                fencing_token=claimed.fencing_token,
                result=result,
                now=now,
            )
        if not completed:
            return False
        async with self._session_factory() as session:
            return await KBLeaseService(session).release(claimed.lease)

    async def recover_expired(self, *, now: datetime) -> list[str]:
        async with self._session_factory() as session:
            return await UpdateJobRepository(session).mark_expired_for_recovery(now=now)

