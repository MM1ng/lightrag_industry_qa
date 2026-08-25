from __future__ import annotations

import pytest
from industrial_rag.db.models import Base, KnowledgeBase, VectorIndexGenerationStatus
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_generation_repository_creates_and_activates_exact_generation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            knowledge_base = KnowledgeBase(
                id="a" * 32,
                name="test",
                workspace_path="C:/tmp/nano/workspace",
                upload_path="C:/tmp/uploads",
                parsed_path="C:/tmp/parsed",
            )
            session.add(knowledge_base)
            await session.flush()
            repository = VectorIndexGenerationRepository(session)
            generation = await repository.create_shadow(
                knowledge_base_id=knowledge_base.id,
                backend="qdrant",
                generation="g20260731abc",
                workspace_path="C:/tmp/qdrant/workspace",
                collections={"chunks": "ira_p3test_kb_chunks"},
                document_manifest_hash="a" * 64,
                child_chunks_manifest_hash="b" * 64,
                embedding_config_hash="c" * 64,
                chunking_config_hash="d" * 64,
                created_by_task_id=None,
            )

            await repository.activate(generation)

            assert generation.status is VectorIndexGenerationStatus.active
            assert generation.activated_at is not None
    finally:
        await engine.dispose()
