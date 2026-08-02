"""Persistence operations for two-stage Generation garbage collection plans."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import GCPlan, GCPlanStatus


class GCPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> GCPlan:
        plan = GCPlan(status=GCPlanStatus.planned, **values)
        self._session.add(plan)
        await self._session.flush()
        return plan

    async def get(self, plan_id: str) -> GCPlan | None:
        return await self._session.get(GCPlan, plan_id)

    async def approve(
        self,
        plan_id: str,
        *,
        approved_by: str,
        now: datetime,
    ) -> GCPlan | None:
        plan = await self.get(plan_id)
        if (
            plan is None
            or plan.status is not GCPlanStatus.planned
            or plan.expires_at <= now
        ):
            return None
        plan.status = GCPlanStatus.approved
        plan.approved_by = approved_by
        plan.approved_at = now
        await self._session.flush()
        return plan

    async def finalize(
        self,
        plan_id: str,
        *,
        status: GCPlanStatus,
        result: dict[str, Any],
        executed_at: datetime,
    ) -> GCPlan | None:
        plan = await self.get(plan_id)
        if plan is None:
            return None
        plan.status = status
        plan.result = result
        plan.executed_at = executed_at
        await self._session.flush()
        return plan

