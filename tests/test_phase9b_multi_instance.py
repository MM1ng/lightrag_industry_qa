"""Active/Candidate routing and cross-instance runtime consistency tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from industrial_rag.answer_grounding import AnswerPoint
from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    Document,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import get_session_factory, init_db, reset_for_testing
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _GenerationRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.initialized = False
        self.closed = True

    async def query(self, question: str, *, mode: str) -> QueryResult:
        generation = self.settings.qdrant_generation or "none"
        answer = f"{question}:{generation}"
        return QueryResult(
            answer=answer,
            citations=(Citation("manual.pdf", 1, f"chunk-{generation}"),),
            mode=mode,
            answer_points=(AnswerPoint("P1", answer, ("E1",), "supported"),),
        )


@pytest_asyncio.fixture
async def multi_instance_state(tmp_path):
    database_path = tmp_path / "multi-instance.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_id = "b" * 32
    new_id = "c" * 32
    kb_id = "a" * 32
    async with factory() as session:
        kb = KnowledgeBase(
            id=kb_id,
            name="multi-instance",
            status="ready",
            workspace_path=str(tmp_path / "old"),
            upload_path=str(tmp_path / "uploads"),
            parsed_path=str(tmp_path / "parsed"),
            vector_backend="qdrant",
            active_vector_generation_id=old_id,
            generation_epoch=1,
        )
        old = VectorIndexGeneration(
            id=old_id,
            knowledge_base_id=kb_id,
            backend="qdrant",
            generation="g-old",
            status=VectorIndexGenerationStatus.active,
            workspace_path=str(tmp_path / "old"),
            collections={"chunks": "old_chunks"},
            document_manifest_hash="1" * 64,
            child_chunks_manifest_hash="2" * 64,
            embedding_config_hash="3" * 64,
            chunking_config_hash="4" * 64,
        )
        new = VectorIndexGeneration(
            id=new_id,
            knowledge_base_id=kb_id,
            backend="qdrant",
            generation="g-new",
            status=VectorIndexGenerationStatus.ready,
            workspace_path=str(tmp_path / "new"),
            collections={"chunks": "new_chunks"},
            document_manifest_hash="5" * 64,
            child_chunks_manifest_hash="6" * 64,
            embedding_config_hash="7" * 64,
            chunking_config_hash="8" * 64,
        )
        session.add_all([kb, old, new])
        await session.commit()
    yield factory, kb_id, old_id, new_id, tmp_path
    await engine.dispose()


def _base_settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="offline-provider-key",
        working_dir=tmp_path,
        vector_backend="qdrant",
        qdrant_url="http://127.0.0.1:1",
    )


@pytest.mark.asyncio
async def test_candidate_query_does_not_change_active_pointer(multi_instance_state) -> None:
    from industrial_rag.services.query_application_service import (
        QueryApplicationService,
    )

    factory, kb_id, old_id, new_id, tmp_path = multi_instance_state
    manager = KnowledgeBaseRuntimeManager(service_factory=_GenerationRuntime)
    async with factory() as session:
        service = QueryApplicationService(
            session,
            base_settings=_base_settings(tmp_path),
            runtime_manager=manager,
        )
        result = await service.query_generation(kb_id, new_id, "candidate")
        kb = await session.get(KnowledgeBase, kb_id)

    assert result.generation_id == new_id
    assert result.generation_name == "g-new"
    assert result.result.answer == "candidate:g-new"
    assert kb is not None
    assert kb.active_vector_generation_id == old_id
    await manager.close_all()


@pytest.mark.asyncio
async def test_failed_candidate_generation_is_rejected_with_conflict(multi_instance_state) -> None:
    from industrial_rag.errors import AppError
    from industrial_rag.services.query_application_service import QueryApplicationService

    factory, kb_id, _old_id, new_id, tmp_path = multi_instance_state
    manager = KnowledgeBaseRuntimeManager(service_factory=_GenerationRuntime)
    async with factory() as session:
        candidate = await session.get(VectorIndexGeneration, new_id)
        assert candidate is not None
        candidate.status = VectorIndexGenerationStatus.failed
        await session.commit()
        with pytest.raises(AppError) as error:
            await QueryApplicationService(
                session,
                base_settings=_base_settings(tmp_path),
                runtime_manager=manager,
            ).query_generation(kb_id, new_id, "candidate")
    assert error.value.status_code == 409
    await manager.close_all()


@pytest.mark.asyncio
async def test_two_runtime_managers_follow_promote_and_rollback_without_restart(
    multi_instance_state,
) -> None:
    from industrial_rag.services.query_application_service import (
        QueryApplicationService,
    )

    factory, kb_id, old_id, new_id, tmp_path = multi_instance_state
    manager_a = KnowledgeBaseRuntimeManager(service_factory=_GenerationRuntime)
    manager_b = KnowledgeBaseRuntimeManager(service_factory=_GenerationRuntime)

    async def query(manager, question: str):
        async with factory() as session:
            return await QueryApplicationService(
                session,
                base_settings=_base_settings(tmp_path),
                runtime_manager=manager,
            ).query_active(kb_id, question)

    assert (await query(manager_a, "before-a")).generation_id == old_id
    assert (await query(manager_b, "before-b")).generation_id == old_id

    async with factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        old = await session.get(VectorIndexGeneration, old_id)
        new = await session.get(VectorIndexGeneration, new_id)
        assert kb is not None and old is not None and new is not None
        kb.active_vector_generation_id = new_id
        kb.workspace_path = new.workspace_path
        kb.generation_epoch += 1
        old.status = VectorIndexGenerationStatus.archived
        new.status = VectorIndexGenerationStatus.active
        await session.commit()

    promoted_b = await query(manager_b, "after-promote")
    assert promoted_b.generation_id == new_id
    assert promoted_b.result.answer == "after-promote:g-new"

    async with factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        old = await session.get(VectorIndexGeneration, old_id)
        new = await session.get(VectorIndexGeneration, new_id)
        assert kb is not None and old is not None and new is not None
        kb.active_vector_generation_id = old_id
        kb.workspace_path = old.workspace_path
        kb.generation_epoch += 1
        old.status = VectorIndexGenerationStatus.active
        new.status = VectorIndexGenerationStatus.archived
        await session.commit()

    assert (await query(manager_a, "after-rollback-a")).generation_id == old_id
    assert (await query(manager_b, "after-rollback-b")).generation_id == old_id
    await manager_a.close_all()
    await manager_b.close_all()


@pytest.mark.asyncio
async def test_admin_candidate_query_route_returns_actual_generation_without_switch(
    tmp_path, monkeypatch
) -> None:
    from industrial_rag.api import create_app

    database_path = tmp_path / "candidate-route.db"
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    reset_for_testing()
    await init_db()
    factory = get_session_factory()
    kb_id, active_id, candidate_id = "d" * 32, "e" * 32, "f" * 32
    async with factory() as session:
        session.add_all(
            [
                KnowledgeBase(
                    id=kb_id,
                    name="candidate-route",
                    status="ready",
                    workspace_path=str(tmp_path / "active"),
                    upload_path=str(tmp_path / "uploads"),
                    parsed_path=str(tmp_path / "parsed"),
                    vector_backend="qdrant",
                    active_vector_generation_id=active_id,
                    generation_epoch=4,
                ),
                VectorIndexGeneration(
                    id=active_id,
                    knowledge_base_id=kb_id,
                    backend="qdrant",
                    generation="g-active",
                    status=VectorIndexGenerationStatus.active,
                    workspace_path=str(tmp_path / "active"),
                    collections={"chunks": "active_chunks"},
                    document_manifest_hash="1" * 64,
                    child_chunks_manifest_hash="2" * 64,
                    embedding_config_hash="3" * 64,
                    chunking_config_hash="4" * 64,
                ),
                VectorIndexGeneration(
                    id=candidate_id,
                    knowledge_base_id=kb_id,
                    backend="qdrant",
                    generation="g-candidate",
                    status=VectorIndexGenerationStatus.ready,
                    workspace_path=str(tmp_path / "candidate"),
                    collections={"chunks": "candidate_chunks"},
                    document_manifest_hash="5" * 64,
                    child_chunks_manifest_hash="6" * 64,
                    embedding_config_hash="7" * 64,
                    chunking_config_hash="8" * 64,
                ),
                Document(
                    id="1" * 32,
                    knowledge_base_id=kb_id,
                    original_file_name="manual.pdf",
                    logical_name="manual.pdf",
                    stored_file_name="manual.pdf",
                    file_path=str(tmp_path / "manual.pdf"),
                    file_hash="9" * 64,
                    status="indexed",
                    is_active=True,
                    parse_status="done",
                    index_status="done",
                ),
            ]
        )
        await session.commit()

    settings = Settings(
        api_key="offline-provider-key",
        service_api_key="service-test-key",
        admin_api_key="admin-test-key",
        working_dir=tmp_path,
        vector_backend="qdrant",
        qdrant_url="http://127.0.0.1:1",
    )
    manager = KnowledgeBaseRuntimeManager(service_factory=_GenerationRuntime)
    app = create_app(settings=settings)
    app.state.service_api_key = settings.service_api_key
    app.state.admin_api_key = settings.admin_api_key
    app.state.resolved_settings = settings
    app.state.runtime_manager = manager
    route = "/v1/knowledge-bases/{kb_id}/generations/{generation_id}/query"
    assert route in app.openapi()["paths"]
    assert "QueryResponse" in json.dumps(app.openapi()["paths"][route])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/knowledge-bases/{kb_id}/generations/{candidate_id}/query",
            json={"query": "candidate"},
            headers={"Authorization": "Bearer admin-test-key"},
        )

    assert response.status_code == 200
    assert response.json()["generation_id"] == candidate_id
    assert response.json()["answer"] == "candidate:g-candidate"
    assert response.json()["citations"][0]["document_id"] == "1" * 32
    assert response.json()["citations"][0]["generation_id"] == candidate_id
    async with factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        assert kb is not None
        assert kb.active_vector_generation_id == active_id
    await manager.close_all()
    reset_for_testing()
