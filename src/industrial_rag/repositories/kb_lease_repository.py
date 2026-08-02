"""Atomic SQLite-compatible persistence for KB operation leases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import (
    KBOperationLease,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)


class KBLeaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_row(self, kb_id: str) -> None:
        statement = (
            sqlite_insert(KBOperationLease)
            .values(knowledge_base_id=kb_id, fencing_token=0)
            .on_conflict_do_nothing(index_elements=["knowledge_base_id"])
        )
        await self._session.execute(statement)
        await self._session.commit()

    async def acquire(
        self,
        kb_id: str,
        *,
        owner: str,
        lease_token: str,
        operation: str,
        job_id: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> KBOperationLease | None:
        await self.ensure_row(kb_id)
        statement = (
            update(KBOperationLease)
            .where(
                KBOperationLease.knowledge_base_id == kb_id,
                or_(
                    KBOperationLease.lock_owner.is_(None),
                    KBOperationLease.expires_at.is_(None),
                    KBOperationLease.expires_at <= now,
                ),
            )
            .values(
                lock_owner=owner,
                lease_token=lease_token,
                fencing_token=KBOperationLease.fencing_token + 1,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires_at,
                operation=operation,
                job_id=job_id,
            )
            .returning(KBOperationLease)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        await self._session.commit()
        return row

    async def heartbeat(
        self,
        *,
        kb_id: str,
        owner: str,
        lease_token: str,
        fencing_token: int,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        statement = (
            update(KBOperationLease)
            .where(
                KBOperationLease.knowledge_base_id == kb_id,
                KBOperationLease.lock_owner == owner,
                KBOperationLease.lease_token == lease_token,
                KBOperationLease.fencing_token == fencing_token,
                KBOperationLease.expires_at > now,
            )
            .values(heartbeat_at=now, expires_at=expires_at)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def is_current(
        self,
        *,
        kb_id: str,
        owner: str,
        lease_token: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        statement = select(KBOperationLease.knowledge_base_id).where(
            KBOperationLease.knowledge_base_id == kb_id,
            KBOperationLease.lock_owner == owner,
            KBOperationLease.lease_token == lease_token,
            KBOperationLease.fencing_token == fencing_token,
            KBOperationLease.expires_at > now,
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def release(
        self,
        *,
        kb_id: str,
        owner: str,
        lease_token: str,
        fencing_token: int,
    ) -> bool:
        statement = (
            update(KBOperationLease)
            .where(
                KBOperationLease.knowledge_base_id == kb_id,
                KBOperationLease.lock_owner == owner,
                KBOperationLease.lease_token == lease_token,
                KBOperationLease.fencing_token == fencing_token,
            )
            .values(
                lock_owner=None,
                lease_token=None,
                acquired_at=None,
                heartbeat_at=None,
                expires_at=None,
                operation=None,
                job_id=None,
            )
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        if result.rowcount == 1:
            return True
        row = await self._session.get(KBOperationLease, kb_id)
        return bool(
            row is not None
            and row.lock_owner is None
            and row.fencing_token == fencing_token
        )

    async def update_generation_status(
        self,
        generation_id: str,
        *,
        kb_id: str,
        owner: str,
        lease_token: str,
        fencing_token: int,
        status: VectorIndexGenerationStatus,
        now: datetime,
    ) -> bool:
        current_lease = exists(
            select(KBOperationLease.knowledge_base_id).where(
                KBOperationLease.knowledge_base_id == kb_id,
                KBOperationLease.lock_owner == owner,
                KBOperationLease.lease_token == lease_token,
                KBOperationLease.fencing_token == fencing_token,
                KBOperationLease.expires_at > now,
            )
        )
        statement = (
            update(VectorIndexGeneration)
            .where(
                VectorIndexGeneration.id == generation_id,
                VectorIndexGeneration.knowledge_base_id == kb_id,
                current_lease,
            )
            .values(status=status)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

