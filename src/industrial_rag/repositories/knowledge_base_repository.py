"""Repository layer: async DB operations for KnowledgeBase."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import KBStatus, KnowledgeBase, LifecycleTask, TaskStatus


class KnowledgeBaseRepository:
    """Async CRUD for KnowledgeBase rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, name: str, description: str | None = None, **extra: Any) -> KnowledgeBase:
        kb = KnowledgeBase(
            name=name.strip(),
            description=description.strip() if description else None,
            status=KBStatus.creating,
            **extra,
        )
        self._session.add(kb)
        await self._session.flush()
        return kb

    async def get(self, kb_id: str) -> KnowledgeBase | None:
        return await self._session.get(KnowledgeBase, kb_id)

    async def list_all(
        self,
        *,
        include_deleted: bool = False,
        status_filter: KBStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[KnowledgeBase]:
        stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        if not include_deleted:
            stmt = stmt.where(KnowledgeBase.status != KBStatus.deleted)
        if status_filter is not None:
            stmt = stmt.where(KnowledgeBase.status == status_filter)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self, *, include_deleted: bool = False) -> int:
        stmt = select(func.count(KnowledgeBase.id))
        if not include_deleted:
            stmt = stmt.where(KnowledgeBase.status != KBStatus.deleted)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def update(self, kb_id: str, **values: Any) -> KnowledgeBase | None:
        kb = await self.get(kb_id)
        if kb is None:
            return None
        for key, val in values.items():
            if hasattr(kb, key):
                setattr(kb, key, val)
        kb.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return kb

    async def soft_delete(self, kb_id: str) -> KnowledgeBase | None:
        kb = await self.get(kb_id)
        if kb is None:
            return None
        kb.status = KBStatus.deleting
        kb.deleted_at = datetime.now(tz=UTC)
        kb.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return kb

    async def mark_deleted(self, kb_id: str) -> KnowledgeBase | None:
        kb = await self.get(kb_id)
        if kb is None:
            return None
        kb.status = KBStatus.deleted
        kb.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return kb

    async def has_active_tasks(self, kb_id: str) -> bool:
        stmt = select(func.count(LifecycleTask.id)).where(
            LifecycleTask.knowledge_base_id == kb_id,
            LifecycleTask.status.in_(
                [TaskStatus.pending, TaskStatus.running, TaskStatus.retrying]
            ),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def get_legacy_default(self) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(KnowledgeBase.is_legacy_default == True)  # noqa: E712
        result = await self._session.execute(stmt)
        return result.scalars().first()
