"""Canonical validation evidence and non-bypassable Promote gate tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    KnowledgeBase,
    ValidationRunStatus,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.validation_run_repository import ValidationRunRepository
from industrial_rag.services.canonical_validation_runner import CanonicalValidationRunner
from industrial_rag.services.generation_content_fingerprint import (
    GenerationContentFingerprintService,
    stable_hash,
)
from industrial_rag.services.golden_set_policy import (
    CANONICAL_QUESTION_IDS,
    RUNNER_VERSION,
    GoldenSetPolicy,
    load_canonical_policy,
)
from industrial_rag.services.validation_gate_service import ValidationGateService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _Qdrant:
    def __init__(self) -> None:
        self.points = [
            SimpleNamespace(
                id="point-1",
                payload={"id": "chunk-1", "generation": "g1", "content": "pump"},
                vector=[0.1, 0.2],
            )
        ]

    async def collection_exists(self, name: str) -> bool:
        return name == "chunks-g1"

    async def scroll(self, **kwargs):
        return list(self.points), None

    async def close(self) -> None:
        return None


@pytest_asyncio.fixture
async def validation_state(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'gate.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = _Qdrant()
    settings = Settings(
        api_key="provider-test-key",
        working_dir=tmp_path,
        vector_backend="qdrant",
        qdrant_url="http://qdrant.invalid",
        validation_artifact_dir=tmp_path / "validation",
    )
    kb_id, generation_id = "a" * 32, "b" * 32
    async with factory() as session:
        session.add_all(
            [
                KnowledgeBase(
                    id=kb_id,
                    name="gate",
                    status="ready",
                    workspace_path=str(tmp_path / "workspace"),
                    upload_path=str(tmp_path / "uploads"),
                    parsed_path=str(tmp_path / "parsed"),
                    vector_backend="qdrant",
                ),
                VectorIndexGeneration(
                    id=generation_id,
                    knowledge_base_id=kb_id,
                    backend="qdrant",
                    generation="g1",
                    status=VectorIndexGenerationStatus.ready,
                    workspace_path=str(tmp_path / "workspace"),
                    collections={"chunks": "chunks-g1"},
                    document_manifest_hash="1" * 64,
                    child_chunks_manifest_hash="2" * 64,
                    embedding_config_hash="3" * 64,
                    chunking_config_hash="4" * 64,
                ),
            ]
        )
        await session.commit()
    yield factory, settings, qdrant, kb_id, generation_id, tmp_path
    await engine.dispose()


def test_canonical_policy_is_exactly_the_frozen_twenty() -> None:
    policy = load_canonical_policy()
    assert tuple(item["id"] for item in policy.questions) == CANONICAL_QUESTION_IDS
    assert len(policy.questions) == 20
    assert len(policy.source_sha256) == 64


@pytest.mark.asyncio
async def test_missing_http_runner_configuration_never_defaults_to_pass(tmp_path) -> None:
    settings = Settings(api_key="provider-test-key", working_dir=tmp_path)
    report = await CanonicalValidationRunner(settings, load_canonical_policy())(
        "a" * 32,
        SimpleNamespace(id="b" * 32),
    )
    assert report["runner_configured"] is False
    assert report["http_success_rate"] == 0.0
    assert report["no_5xx"] is False


@pytest.mark.asyncio
async def test_canonical_http_record_contains_required_trace_and_safety_fields(
    tmp_path, monkeypatch
) -> None:
    response = httpx.Response(
        200,
        json={
            "request_id": "req-123",
            "trace_id": "trace-456",
            "status": "success",
            "answer": "可验证答案",
            "generation_id": "b" * 32,
            "citations": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "generation_id": "b" * 32,
                    "document_name": "source.pdf",
                    "page": 1,
                }
            ],
        },
    )

    class _HTTPClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(
        "industrial_rag.services.canonical_validation_runner.AsyncClient",
        _HTTPClient,
    )
    policy = GoldenSetPolicy(
        questions=(
            {
                "id": "T001",
                "question": "test",
                "expects_evidence": True,
                "expected_citations": [
                    {"source_file": "source.pdf", "page_number": 1}
                ],
            },
        ),
        source_sha256="1" * 64,
        source_path=tmp_path / "test-golden.json",
        version="test",
    )
    settings = Settings(
        api_key="provider-test-key",
        admin_api_key="admin-test-key",
        validation_base_url="http://validation.invalid",
        working_dir=tmp_path,
    )
    report = await CanonicalValidationRunner(settings, policy)(
        "a" * 32,
        SimpleNamespace(id="b" * 32),
    )
    record = report["results"][0]
    assert record["request_id"] == "req-123"
    assert record["trace_id"] == "trace-456"
    assert record["answer_status"] == "success"
    assert record["safety_result"] == "allowed"
    assert record["failure_reason"] is None


@pytest.mark.asyncio
async def test_promote_gate_rejects_missing_validation(validation_state) -> None:
    factory, settings, qdrant, kb_id, generation_id, _ = validation_state
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError) as caught:
            await ValidationGateService(
                session,
                settings=settings,
                qdrant_client_factory=lambda: qdrant,
            ).require_eligible(kb_id, generation)
    assert caught.value.code == AppErrorCode.generation_validation_required


async def _record_valid_run(state) -> Path:
    factory, settings, qdrant, kb_id, generation_id, tmp_path = state
    policy = load_canonical_policy()
    artifact = tmp_path / "validation" / "run.jsonl"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"id":"S001","status_code":200}\n', encoding="utf-8")
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        evidence = await GenerationContentFingerprintService(
            session,
            settings=settings,
            qdrant_client_factory=lambda: qdrant,
        ).calculate(kb_id, generation)
        fingerprint_payload = {
            "app_git_commit": evidence.app_git_commit,
            "strategy": evidence.strategy_fingerprint,
            "generation_manifest": evidence.generation_manifest_hash,
            "qdrant_content": evidence.qdrant_content_fingerprint,
            "document_registry": evidence.document_registry_fingerprint,
            "generation_content_epoch": evidence.generation_content_epoch,
            "qdrant_point_count": evidence.qdrant_point_count,
        }
        generation.validated_fingerprint = stable_hash(fingerprint_payload)
        repository = ValidationRunRepository(session)
        run = await repository.create(
            knowledge_base_id=kb_id,
            generation_id=generation_id,
            golden_set_version=policy.version,
            golden_set_sha256=policy.source_sha256,
            runner_version=RUNNER_VERSION,
            app_git_commit=evidence.app_git_commit,
            configured_model=settings.llm_model,
            strategy_fingerprint=evidence.strategy_fingerprint,
            generation_manifest_hash=evidence.generation_manifest_hash,
            qdrant_content_fingerprint=evidence.qdrant_content_fingerprint,
            document_registry_fingerprint=evidence.document_registry_fingerprint,
            generation_content_epoch=evidence.generation_content_epoch,
            actor="admin:test",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await repository.finalize(
            run.id,
            passed=True,
            metrics={"question_count": 20},
            artifact_path=str(artifact),
            artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            finished_at=datetime.now(UTC),
        )
        await session.commit()
    return artifact


@pytest.mark.asyncio
async def test_valid_evidence_is_eligible_and_artifact_tamper_blocks(validation_state) -> None:
    factory, settings, qdrant, kb_id, generation_id, _ = validation_state
    artifact = await _record_valid_run(validation_state)
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        run = await ValidationGateService(
            session,
            settings=settings,
            qdrant_client_factory=lambda: qdrant,
        ).require_eligible(kb_id, generation)
        assert run.status is ValidationRunStatus.passed

    artifact.write_text("tampered\n", encoding="utf-8")
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError) as caught:
            await ValidationGateService(
                session,
                settings=settings,
                qdrant_client_factory=lambda: qdrant,
            ).require_eligible(kb_id, generation)
    assert caught.value.code == AppErrorCode.generation_validation_stale


@pytest.mark.asyncio
async def test_qdrant_change_after_validation_blocks_promote(validation_state) -> None:
    factory, settings, qdrant, kb_id, generation_id, _ = validation_state
    await _record_valid_run(validation_state)
    qdrant.points[0].payload["content"] = "changed after validation"
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError) as caught:
            await ValidationGateService(
                session,
                settings=settings,
                qdrant_client_factory=lambda: qdrant,
            ).require_eligible(kb_id, generation)
    assert caught.value.code == AppErrorCode.generation_validation_stale


@pytest.mark.asyncio
async def test_golden_policy_change_invalidates_previous_run(validation_state, monkeypatch) -> None:
    factory, settings, qdrant, kb_id, generation_id, _ = validation_state
    await _record_valid_run(validation_state)
    original = load_canonical_policy()
    changed = GoldenSetPolicy(
        version="phase9b-canonical-20-v2",
        source_path=original.source_path,
        source_sha256="f" * 64,
        questions=original.questions,
    )
    monkeypatch.setattr(
        "industrial_rag.services.validation_gate_service.load_canonical_policy",
        lambda: changed,
    )
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError) as caught:
            await ValidationGateService(
                session,
                settings=settings,
                qdrant_client_factory=lambda: qdrant,
            ).require_eligible(kb_id, generation)
    assert caught.value.code == AppErrorCode.generation_validation_required
