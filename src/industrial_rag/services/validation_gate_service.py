"""Non-bypassable Promote eligibility checks for canonical validation evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from industrial_rag.config import Settings
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.validation_run_repository import ValidationRunRepository
from industrial_rag.services.generation_content_fingerprint import (
    GenerationContentFingerprintService,
    stable_hash,
)
from industrial_rag.services.golden_set_policy import RUNNER_VERSION, load_canonical_policy


class ValidationGateService:
    def __init__(self, session, *, settings: Settings, qdrant_client_factory) -> None:
        self._runs = ValidationRunRepository(session)
        self._fingerprints = GenerationContentFingerprintService(
            session,
            settings=settings,
            qdrant_client_factory=qdrant_client_factory,
        )
        self._settings = settings

    async def require_eligible(self, kb_id: str, generation):
        policy = load_canonical_policy()
        now = datetime.now(UTC)
        run = await self._runs.latest_eligible(
            generation.id,
            golden_set_version=policy.version,
            golden_set_sha256=policy.source_sha256,
            now=now,
        )
        if run is None:
            raise AppError(
                AppErrorCode.generation_validation_required,
                "Generation 没有当前有效的 canonical validation run。",
                status_code=409,
            )
        artifact = Path(run.result_artifact_path or "")
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != run.result_artifact_sha256:
            self._stale("validation artifact missing or changed")
        current = await self._fingerprints.calculate(kb_id, generation)
        expected = {
            "app_git_commit": current.app_git_commit,
            "strategy": current.strategy_fingerprint,
            "generation_manifest": current.generation_manifest_hash,
            "qdrant_content": current.qdrant_content_fingerprint,
            "document_registry": current.document_registry_fingerprint,
            "generation_content_epoch": current.generation_content_epoch,
            "qdrant_point_count": current.qdrant_point_count,
        }
        comparisons = {
            "runner": run.runner_version == RUNNER_VERSION,
            "app_git_commit": run.app_git_commit == current.app_git_commit,
            "model": run.configured_model == self._settings.llm_model,
            "strategy": run.strategy_fingerprint == current.strategy_fingerprint,
            "manifest": run.generation_manifest_hash == current.generation_manifest_hash,
            "qdrant": run.qdrant_content_fingerprint == current.qdrant_content_fingerprint,
            "documents": run.document_registry_fingerprint == current.document_registry_fingerprint,
            "content_epoch": run.generation_content_epoch == current.generation_content_epoch,
            "validated_fingerprint": generation.validated_fingerprint == stable_hash(expected),
        }
        if not all(comparisons.values()):
            failed = ",".join(name for name, passed in comparisons.items() if not passed)
            self._stale(f"validation evidence no longer matches: {failed}")
        return run

    @staticmethod
    def _stale(message: str) -> None:
        raise AppError(
            AppErrorCode.generation_validation_stale,
            message,
            status_code=409,
        )
