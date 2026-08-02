"""Durable per-KB lease API with monotonically increasing fencing tokens."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import VectorIndexGenerationStatus
from industrial_rag.repositories.kb_lease_repository import KBLeaseRepository


@dataclass(frozen=True, slots=True)
class LeaseHandle:
    kb_id: str
    owner: str
    lease_token: str
    fencing_token: int
    expires_at: datetime


class KBLeaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = KBLeaseRepository(session)

    async def acquire(
        self,
        kb_id: str,
        *,
        owner: str,
        operation: str,
        now: datetime,
        ttl: timedelta,
        job_id: str | None = None,
    ) -> LeaseHandle | None:
        token = secrets.token_hex(24)
        expires_at = now + ttl
        row = await self._repository.acquire(
            kb_id,
            owner=owner,
            lease_token=token,
            operation=operation,
            job_id=job_id,
            now=now,
            expires_at=expires_at,
        )
        if row is None:
            return None
        return LeaseHandle(
            kb_id=kb_id,
            owner=owner,
            lease_token=token,
            fencing_token=row.fencing_token,
            expires_at=expires_at,
        )

    async def heartbeat(
        self,
        handle: LeaseHandle,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> bool:
        return await self._repository.heartbeat(
            kb_id=handle.kb_id,
            owner=handle.owner,
            lease_token=handle.lease_token,
            fencing_token=handle.fencing_token,
            now=now,
            expires_at=now + ttl,
        )

    async def is_current(self, handle: LeaseHandle, *, now: datetime) -> bool:
        return await self._repository.is_current(
            kb_id=handle.kb_id,
            owner=handle.owner,
            lease_token=handle.lease_token,
            fencing_token=handle.fencing_token,
            now=now,
        )

    async def release(self, handle: LeaseHandle) -> bool:
        return await self._repository.release(
            kb_id=handle.kb_id,
            owner=handle.owner,
            lease_token=handle.lease_token,
            fencing_token=handle.fencing_token,
        )

    async def update_generation_status(
        self,
        generation_id: str,
        handle: LeaseHandle,
        status: VectorIndexGenerationStatus,
        *,
        now: datetime,
    ) -> bool:
        return await self._repository.update_generation_status(
            generation_id,
            kb_id=handle.kb_id,
            owner=handle.owner,
            lease_token=handle.lease_token,
            fencing_token=handle.fencing_token,
            status=status,
            now=now,
        )

