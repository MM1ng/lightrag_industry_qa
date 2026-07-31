from industrial_rag.db.models import (
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)


def test_vector_index_generation_models_bind_an_active_generation_by_foreign_key() -> None:
    generation = VectorIndexGeneration(
        knowledge_base_id="a" * 32,
        backend="qdrant",
        generation="g20260731abc",
        status=VectorIndexGenerationStatus.shadow,
        workspace_path="C:/data/kb/qdrant/generations/g20260731abc/workspace",
        collections={"chunks": "ira_p3test_kb_a_chunks"},
        document_manifest_hash="document-hash",
        child_chunks_manifest_hash="children-hash",
        embedding_config_hash="embedding-hash",
        chunking_config_hash="chunking-hash",
        created_by_task_id="b" * 32,
    )
    knowledge_base = KnowledgeBase(
        id="a" * 32,
        name="test",
        workspace_path="C:/data/kb/nano/workspace",
        upload_path="C:/data/kb/uploads",
        parsed_path="C:/data/kb/parsed",
        active_vector_generation_id=generation.id,
    )

    assert generation.status is VectorIndexGenerationStatus.shadow
    assert knowledge_base.active_vector_generation_id == generation.id
