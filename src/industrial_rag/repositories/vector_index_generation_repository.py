"""Persistence operations for immutable vector-index generation records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import (
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)


class VectorIndexGenerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_shadow(self, **values: Any) -> VectorIndexGeneration:
        generation = VectorIndexGeneration(
            status=VectorIndexGenerationStatus.shadow,
            **values,
        )
        self._session.add(generation)
        await self._session.flush()
        return generation

    async def get(self, generation_id: str) -> VectorIndexGeneration | None:
        return await self._session.get(VectorIndexGeneration, generation_id)

    async def get_active(self, knowledge_base_id: str) -> VectorIndexGeneration | None:
        statement = select(VectorIndexGeneration).where(
            VectorIndexGeneration.knowledge_base_id == knowledge_base_id,
            VectorIndexGeneration.status == VectorIndexGenerationStatus.active,
        )
        result = await self._session.execute(statement)
        return result.scalars().first()

    async def list_for_kb(self, knowledge_base_id: str) -> list[VectorIndexGeneration]:
        statement = select(VectorIndexGeneration).where(
            VectorIndexGeneration.knowledge_base_id == knowledge_base_id
        ).order_by(VectorIndexGeneration.created_at.desc())
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def activate(self, generation: VectorIndexGeneration) -> None:
        now = datetime.now(tz=UTC)
        active = await self.get_active(generation.knowledge_base_id)
        if active is not None and active.id != generation.id:
            active.status = VectorIndexGenerationStatus.retired
            active.retired_at = now
        generation.status = VectorIndexGenerationStatus.active
        generation.activated_at = now
        generation.retired_at = None
        generation.last_error = None
        await self._session.flush()

    async def update_workspace_path(self, generation: VectorIndexGeneration, workspace_path: str) -> None:
        generation.workspace_path = workspace_path
        await self._session.flush()

    async def mark_failed(self, generation: VectorIndexGeneration, error: str) -> None:
        generation.status = VectorIndexGenerationStatus.failed
        generation.last_error = error[:500]
        await self._session.flush()

    async def list_cleanup_candidates(self, knowledge_base_id: str) -> list[VectorIndexGeneration]:
        return await self.list_for_kb(knowledge_base_id)
