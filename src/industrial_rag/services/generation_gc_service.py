"""Persistent two-stage, exact-name Generation garbage collection."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient

from industrial_rag.config import Settings
from industrial_rag.db.models import GCPlanStatus, VectorIndexGenerationStatus
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.operational_metrics import operational_metrics
from industrial_rag.repositories.gc_plan_repository import GCPlanRepository
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.services.generation_content_fingerprint import (
    GenerationContentFingerprintService,
    stable_hash,
)
from industrial_rag.services.kb_lease_service import KBLeaseService
from industrial_rag.storage_layout import is_safe_to_delete


class GenerationGCService:
    def __init__(self, session, *, settings: Settings | None = None, qdrant_client_factory=None) -> None:
        self._session = session
        self._settings = settings or Settings.from_env()
        self._plans = GCPlanRepository(session)
        self._kbs = KnowledgeBaseRepository(session)
        self._generations = VectorIndexGenerationRepository(session)
        self._qdrant_client_factory = qdrant_client_factory

    async def plan(
        self,
        kb_id: str,
        *,
        actor: str,
        failed_retention_days: int = 7,
        archived_keep_count: int = 2,
        plan_ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        kb = await self._kbs.get(kb_id)
        if kb is None:
            raise AppError(AppErrorCode.knowledge_base_not_found, "知识库不存在。")
        now = datetime.now(UTC)
        generations = await self._generations.list_for_kb(kb_id)
        protected_ids = {
            value
            for value in (
                kb.active_vector_generation_id,
                kb.last_rollback_target_generation_id,
            )
            if value
        }
        archived = [
            generation
            for generation in generations
            if generation.status in {
                VectorIndexGenerationStatus.archived,
                VectorIndexGenerationStatus.retired,
            }
        ]
        archived.sort(key=lambda item: item.created_at, reverse=True)
        retained_archived_ids = {
            item.id
            for item in [entry for entry in archived if entry.id not in protected_ids][
                :archived_keep_count
            ]
        }
        protected_ids.update(retained_archived_ids)
        cutoff = now - timedelta(days=failed_retention_days)
        items: list[dict[str, Any]] = []
        for generation in generations:
            created_at = generation.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            reason = None
            if generation.id in protected_ids:
                continue
            if generation.protect_from_delete or generation.audit_frozen:
                continue
            retention_until = generation.retention_until
            if retention_until is not None:
                if retention_until.tzinfo is None:
                    retention_until = retention_until.replace(tzinfo=UTC)
                if retention_until > now:
                    continue
            if generation.status in {
                VectorIndexGenerationStatus.failed,
                VectorIndexGenerationStatus.shadow,
                VectorIndexGenerationStatus.building,
            } and created_at <= cutoff:
                reason = "expired_non_active_candidate"
            elif (
                generation.status
                in {
                    VectorIndexGenerationStatus.archived,
                    VectorIndexGenerationStatus.retired,
                }
                and generation.id not in retained_archived_ids
            ):
                reason = "archived_retention_exceeded"
            if reason is None:
                continue
            evidence = await self._fingerprints().calculate(kb_id, generation)
            items.append(
                {
                    "generation_id": generation.id,
                    "generation": generation.generation,
                    "status": generation.status.value,
                    "workspace_path": generation.workspace_path,
                    "collections": sorted((generation.collections or {}).values()),
                    "content_epoch": int(generation.content_epoch or 0),
                    "qdrant_content_fingerprint": evidence.qdrant_content_fingerprint,
                    "reason": reason,
                }
            )
        items.sort(key=lambda item: item["generation_id"])
        policy = {
            "failed_retention_days": failed_retention_days,
            "archived_keep_count": archived_keep_count,
            "protected_generation_ids": sorted(protected_ids),
            "exact_name_only": True,
        }
        manifest_hash = stable_hash(
            {"knowledge_base_id": kb_id, "policy": policy, "items": items}
        )
        record = await self._plans.create(
            knowledge_base_id=kb_id,
            policy=policy,
            items=items,
            manifest_hash=manifest_hash,
            created_by=actor,
            expires_at=now + timedelta(minutes=plan_ttl_minutes),
        )
        await self._session.commit()
        operational_metrics.increment("gc_plan_total")
        return self._summary(record)

    async def execute(
        self,
        kb_id: str,
        plan_id: str,
        *,
        manifest_hash: str,
        actor: str,
    ) -> dict[str, Any]:
        plan = await self._plans.get(plan_id)
        if plan is None or plan.knowledge_base_id != kb_id:
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "GC Plan 不存在。",
                status_code=404,
            )
        if plan.manifest_hash != manifest_hash:
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "GC Plan manifest 不匹配。",
                status_code=409,
            )
        now = datetime.now(UTC)
        expires_at = plan.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            plan.status = GCPlanStatus.expired
            await self._session.commit()
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "GC Plan 已过期。",
                status_code=409,
            )
        if plan.status is GCPlanStatus.completed:
            return self._summary(plan)
        lease_service = KBLeaseService(self._session)
        lease = await lease_service.acquire(
            kb_id,
            owner=actor,
            operation="gc_execute",
            now=now,
            ttl=timedelta(minutes=10),
        )
        if lease is None:
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库正在执行其他写操作。",
                status_code=409,
            )
        try:
            plan.status = GCPlanStatus.executing
            plan.approved_by = actor
            plan.approved_at = now
            await self._session.commit()
            previous = {
                item["generation_id"]: item
                for item in (plan.result or {}).get("items", [])
            }
            results: list[dict[str, Any]] = []
            for item in plan.items:
                if previous.get(item["generation_id"], {}).get("status") == "deleted":
                    results.append(previous[item["generation_id"]])
                    continue
                result = await self._delete_exact_item(kb_id, item, lease)
                results.append(result)
                plan.result = {"items": results, "manifest_hash": plan.manifest_hash}
                await self._session.commit()
            status = (
                GCPlanStatus.completed
                if all(item["status"] == "deleted" for item in results)
                else GCPlanStatus.partial_failed
            )
            await self._plans.finalize(
                plan.id,
                status=status,
                result={"items": results, "manifest_hash": plan.manifest_hash},
                executed_at=datetime.now(UTC),
            )
            await self._session.commit()
            operational_metrics.increment("gc_execute_total")
            operational_metrics.increment(
                "gc_execute_completed_total"
                if status is GCPlanStatus.completed
                else "gc_execute_partial_failed_total"
            )
            return self._summary(plan)
        finally:
            await lease_service.release(lease)

    async def _delete_exact_item(self, kb_id: str, item: dict[str, Any], lease) -> dict[str, Any]:
        generation = await self._generations.get(item["generation_id"])
        if generation is None:
            return {**item, "status": "deleted", "detail": "already absent"}
        kb = await self._kbs.get(kb_id)
        if (
            kb is None
            or kb.active_vector_generation_id == generation.id
            or kb.last_rollback_target_generation_id == generation.id
            or generation.protect_from_delete
            or generation.audit_frozen
            or int(generation.content_epoch or 0) != int(item["content_epoch"])
        ):
            return {
                **item,
                "status": "failed",
                "error": "generation became protected or changed",
            }
        if not await KBLeaseService(self._session).is_current(
            lease, now=datetime.now(UTC)
        ):
            return {**item, "status": "failed", "error": "writer lease expired"}
        try:
            evidence = await self._fingerprints().calculate(kb_id, generation)
            if (
                evidence.qdrant_content_fingerprint
                != item.get("qdrant_content_fingerprint")
            ):
                return {
                    **item,
                    "status": "failed",
                    "error": "generation content changed",
                }
            client = self._new_qdrant_client()
            try:
                for collection in item["collections"]:
                    if await client.collection_exists(collection):
                        await client.delete_collection(collection_name=collection)
            finally:
                await client.close()
            workspace = Path(item["workspace_path"])
            if workspace.exists():
                if not is_safe_to_delete(workspace, kb_id=kb_id):
                    raise RuntimeError("workspace path failed exact KB safety check")
                shutil.rmtree(workspace)
            generation.status = VectorIndexGenerationStatus.deleted
            generation.retired_at = datetime.now(UTC)
            await self._session.flush()
            return {**item, "status": "deleted"}
        except Exception as error:
            return {**item, "status": "failed", "error": type(error).__name__}

    def _new_qdrant_client(self):
        if self._qdrant_client_factory is not None:
            return self._qdrant_client_factory()
        return AsyncQdrantClient(
            url=self._settings.qdrant_url,
            api_key=self._settings.qdrant_api_key,
        )

    def _fingerprints(self) -> GenerationContentFingerprintService:
        return GenerationContentFingerprintService(
            self._session,
            settings=self._settings,
            qdrant_client_factory=self._new_qdrant_client,
        )

    @staticmethod
    def _summary(plan) -> dict[str, Any]:
        return {
            "plan_id": plan.id,
            "knowledge_base_id": plan.knowledge_base_id,
            "status": plan.status.value,
            "policy": plan.policy,
            "items": plan.items,
            "manifest_hash": plan.manifest_hash,
            "created_by": plan.created_by,
            "approved_by": plan.approved_by,
            "result": plan.result,
            "expires_at": plan.expires_at,
        }
