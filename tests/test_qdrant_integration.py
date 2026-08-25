"""Opt-in real-Qdrant integration tests (Phase 3V).

These tests run only when IRA_QDRANT_INTEGRATION=1 and target the dedicated
local test instance QDRANT_TEST_URL (default http://127.0.0.1:16333). They
create only collections under a random per-run prefix and precisely delete
every such collection at the end of the module.

Run:
    $env:IRA_QDRANT_INTEGRATION="1"
    python -m pytest tests/test_qdrant_integration.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets

import pytest
from industrial_rag.config import Settings
from industrial_rag.physical_qdrant_storage import (
    PhysicalQdrantVectorDBStorage,
    _point_id,
)
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException

QDRANT_TEST_URL = os.environ.get("QDRANT_TEST_URL", "http://127.0.0.1:16333")

pytestmark = pytest.mark.skipif(
    not os.environ.get("IRA_QDRANT_INTEGRATION"),
    reason="Real Qdrant integration is opt-in via IRA_QDRANT_INTEGRATION=1",
)

CHUNK_META = {"full_doc_id", "content", "file_path"}
ENTITY_META = {"entity_name", "source_id", "content", "file_path"}
RELATION_META = {"src_id", "tgt_id", "source_id", "content", "file_path"}

_NAMESPACE_META = (
    ("chunks", CHUNK_META),
    ("entities", ENTITY_META),
    ("relationships", RELATION_META),
)


class FakeEmbedding:
    """Deterministic 1024-dim embeddings; same text always maps to same vector."""

    embedding_dim = 1024

    async def __call__(
        self, texts: list[str], context: str = "document", _priority: int | None = None
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = [0.0] * 1024
            for byte in digest:
                vector[byte] += 0.1
            vectors.append(vector)
        return vectors


def _new_kb() -> str:
    return secrets.token_hex(16)


def _new_generation() -> str:
    return "g" + secrets.token_hex(12)


def _make_storage(
    url: str,
    prefix: str,
    kb_id: str,
    generation: str,
    namespace: str,
    meta_fields: set[str],
) -> PhysicalQdrantVectorDBStorage:
    return PhysicalQdrantVectorDBStorage(
        namespace=namespace,
        workspace="",
        global_config={
            "vector_db_storage_cls_kwargs": {
                "qdrant_collection_prefix": prefix,
                "qdrant_generation": generation,
                "qdrant_kb_id": kb_id,
                "qdrant_url": url,
                "cosine_better_than_threshold": 0.0,
            }
        },
        embedding_func=FakeEmbedding(),
        meta_fields=meta_fields,
    )


def _qdrant_settings(
    url: str, prefix: str, kb_id: str, generation: str
) -> Settings:
    return Settings(
        api_key="integration-test-key",
        vector_backend=VectorBackend.qdrant,
        qdrant_url=url,
        qdrant_kb_id=kb_id,
        qdrant_generation=generation,
        qdrant_collection_prefix=prefix,
    )


async def _delete_prefix_collections(url: str, prefix: str) -> list[str]:
    client = AsyncQdrantClient(url=url, timeout=10)
    try:
        response = await client.get_collections()
        deleted: list[str] = []
        for item in response.collections:
            if item.name.startswith(prefix):
                await client.delete_collection(item.name)
                deleted.append(item.name)
        return deleted
    finally:
        await client.close()


async def _list_collection_names(url: str) -> list[str]:
    client = AsyncQdrantClient(url=url, timeout=10)
    try:
        response = await client.get_collections()
        return [item.name for item in response.collections]
    finally:
        await client.close()


@pytest.fixture(scope="module")
def test_env() -> dict[str, str]:
    prefix = f"ira_phase3_test_{secrets.token_hex(4)}"
    yield {"prefix": prefix, "url": QDRANT_TEST_URL}
    deleted = asyncio.run(_delete_prefix_collections(QDRANT_TEST_URL, prefix))
    print(f"\n[cleanup] deleted {len(deleted)} collections under prefix {prefix!r}")


# ---------------------------------------------------------------------------
# A. Real collection creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_collection_creation_with_expected_config(test_env: dict[str, str]) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    expected = CollectionNameResolver(prefix).names_for(kb_id=kb_id, generation=generation)
    assert set(expected) == {"chunks", "entities", "relationships"}
    storages = [
        _make_storage(url, prefix, kb_id, generation, namespace, meta)
        for namespace, meta in _NAMESPACE_META
    ]
    try:
        for storage in storages:
            await storage.initialize()
        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            for namespace, name in expected.items():
                assert await client.collection_exists(name), f"missing {name}"
                info = await client.get_collection(name)
                assert info.config.params.vectors.size == 1024
                assert info.config.params.vectors.distance == models.Distance.COSINE
                assert info.status in {"green", "yellow", "grey"}
        finally:
            await client.close()
    finally:
        for storage in storages:
            await storage.finalize()


# ---------------------------------------------------------------------------
# B. Real write + query for chunks/entities/relationships
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_write_query_and_get_across_all_namespaces(
    test_env: dict[str, str],
) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    chunks = _make_storage(url, prefix, kb_id, generation, "chunks", CHUNK_META)
    entities = _make_storage(url, prefix, kb_id, generation, "entities", ENTITY_META)
    relationships = _make_storage(
        url, prefix, kb_id, generation, "relationships", RELATION_META
    )
    storages = [chunks, entities, relationships]
    try:
        for storage in storages:
            await storage.initialize()

        await chunks.upsert(
            {
                "chunk-1": {
                    "content": "泵轴温度检查流程。",
                    "full_doc_id": "doc-a",
                    "file_path": "pump.pdf",
                    "dropped_field": "not-kept",
                },
                "chunk-2": {
                    "content": "密封冷却系统维护。",
                    "full_doc_id": "doc-a",
                    "file_path": "pump.pdf",
                },
            }
        )
        await entities.upsert(
            {
                "entity-泵": {
                    "content": "离心泵",
                    "entity_name": "泵",
                    "source_id": "s-1",
                    "file_path": "pump.pdf",
                }
            }
        )
        await relationships.upsert(
            {
                "rel-1": {
                    "content": "泵 驱动 轴承",
                    "src_id": "泵",
                    "tgt_id": "轴承",
                    "source_id": "s-1",
                    "file_path": "pump.pdf",
                }
            }
        )

        # chunks: query returns payload + distance, top hit is exact match
        results = await chunks.query("泵轴温度检查流程。", top_k=2)
        assert results, "query returned no results"
        assert results[0]["id"] == "chunk-1"
        assert results[0]["content"] == "泵轴温度检查流程。"
        assert results[0]["distance"] is not None
        assert "dropped_field" not in results[0]

        # get_by_ids preserves input order; missing id -> None
        ordered = await chunks.get_by_ids(["chunk-2", "chunk-1", "missing"])
        assert [None if item is None else item["id"] for item in ordered] == [
            "chunk-2",
            "chunk-1",
            None,
        ]

        # entities / relationships readable
        entity = await entities.get_by_id("entity-泵")
        assert entity is not None
        assert entity["entity_name"] == "泵"
        assert entity["content"] == "离心泵"

        relation = await relationships.get_by_id("rel-1")
        assert relation is not None
        assert relation["src_id"] == "泵"
        assert relation["tgt_id"] == "轴承"

        # point ids are stable sha256-based UUIDs
        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            points = (
                await client.retrieve(
                    chunks.collection_name,
                    ids=[_point_id("chunk-1")],
                    with_payload=True,
                )
            )
            assert len(points) == 1
            assert points[0].payload["id"] == "chunk-1"
            assert points[0].id == _point_id("chunk-1")
        finally:
            await client.close()
    finally:
        for storage in storages:
            await storage.finalize()


@pytest.mark.asyncio
async def test_real_count_matches_upserted_points(test_env: dict[str, str]) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    chunks = _make_storage(url, prefix, kb_id, generation, "chunks", CHUNK_META)
    try:
        await chunks.initialize()
        await chunks.upsert(
            {
                f"c-{index}": {"content": f"内容 {index}", "full_doc_id": "doc-a"}
                for index in range(5)
            }
        )
        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            count = (await client.count(chunks.collection_name, exact=True)).count
        finally:
            await client.close()
        assert count == 5
    finally:
        await chunks.finalize()


# ---------------------------------------------------------------------------
# C. KB isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_isolation_between_two_knowledge_bases(test_env: dict[str, str]) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_a, kb_b, generation = _new_kb(), _new_kb(), _new_generation()
    storage_a = _make_storage(url, prefix, kb_a, generation, "chunks", CHUNK_META)
    storage_b = _make_storage(url, prefix, kb_b, generation, "chunks", CHUNK_META)
    try:
        await storage_a.initialize()
        await storage_b.initialize()
        await storage_a.upsert({"a-only": {"content": "A 知识库内容", "full_doc_id": "doc-a"}})
        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            assert storage_a.collection_name != storage_b.collection_name
            count_a = (await client.count(storage_a.collection_name, exact=True)).count
            count_b = (await client.count(storage_b.collection_name, exact=True)).count
        finally:
            await client.close()
        assert count_a == 1
        assert count_b == 0
        assert (await storage_b.get_by_id("a-only")) is None
    finally:
        await storage_a.finalize()
        await storage_b.finalize()


# ---------------------------------------------------------------------------
# D. Generation isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_isolation_between_two_generations(test_env: dict[str, str]) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, g1, g2 = _new_kb(), _new_generation(), _new_generation()
    storage_g1 = _make_storage(url, prefix, kb_id, g1, "chunks", CHUNK_META)
    storage_g2 = _make_storage(url, prefix, kb_id, g2, "chunks", CHUNK_META)
    try:
        await storage_g1.initialize()
        await storage_g2.initialize()
        await storage_g1.upsert({"g1-only": {"content": "G1 内容", "full_doc_id": "doc-a"}})
        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            assert storage_g1.collection_name != storage_g2.collection_name
            count_g1 = (await client.count(storage_g1.collection_name, exact=True)).count
            count_g2 = (await client.count(storage_g2.collection_name, exact=True)).count
        finally:
            await client.close()
        assert count_g1 == 1
        assert count_g2 == 0
        assert (await storage_g2.get_by_id("g1-only")) is None
    finally:
        await storage_g1.finalize()
        await storage_g2.finalize()


# ---------------------------------------------------------------------------
# E. Precise cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_generation_removes_only_registered_collections(
    test_env: dict[str, str],
) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    chunks = _make_storage(url, prefix, kb_id, generation, "chunks", CHUNK_META)
    entities = _make_storage(url, prefix, kb_id, generation, "entities", ENTITY_META)
    relationships = _make_storage(
        url, prefix, kb_id, generation, "relationships", RELATION_META
    )
    # An unrelated collection under the same prefix but a different generation
    other_generation = _new_generation()
    other = _make_storage(url, prefix, kb_id, other_generation, "chunks", CHUNK_META)
    try:
        for storage in (chunks, entities, relationships, other):
            await storage.initialize()
        await chunks.upsert({"keep-me": {"content": "数据", "full_doc_id": "doc-a"}})
        await other.upsert({"other": {"content": "其他代", "full_doc_id": "doc-a"}})

        service = QdrantCollectionService(_qdrant_settings(url, prefix, kb_id, generation))
        await service.delete_generation()

        client = AsyncQdrantClient(url=url, timeout=10)
        try:
            names = {item.name for item in (await client.get_collections()).collections}
        finally:
            await client.close()
        expected = CollectionNameResolver(prefix).names_for(kb_id=kb_id, generation=generation)
        for name in expected.values():
            assert name not in names, f"collection still present after cleanup: {name}"
        assert other.collection_name in names, "unrelated generation collection was deleted"
        # Second confirm: deleting again is a no-op success
        await service.delete_generation()
    finally:
        for storage in (chunks, entities, relationships, other):
            await storage.finalize()


# ---------------------------------------------------------------------------
# F. Restart / client rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_after_client_rebuild_simulates_restart(test_env: dict[str, str]) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    first = _make_storage(url, prefix, kb_id, generation, "chunks", CHUNK_META)
    try:
        await first.initialize()
        await first.upsert(
            {
                "persist-1": {"content": "重启后仍可查询", "full_doc_id": "doc-a"},
                "persist-2": {"content": "第二段内容", "full_doc_id": "doc-a"},
            }
        )
    finally:
        await first.finalize()

    # New storage instance == fresh AsyncQdrantClient after a service restart
    second = _make_storage(url, prefix, kb_id, generation, "chunks", CHUNK_META)
    try:
        await second.initialize()
        assert await second.get_by_id("persist-1") is not None
        results = await second.query("重启后仍可查询", top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == "persist-1"
        assert results[0]["distance"] is not None
    finally:
        await second.finalize()


# ---------------------------------------------------------------------------
# G. Qdrant unavailable -> explicit error, no silent Nano fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qdrant_unavailable_raises_explicit_error(test_env: dict[str, str]) -> None:
    prefix = test_env["prefix"]
    # 127.0.0.1 port 1 is a closed local port; nothing listens there.
    dead_url = "http://127.0.0.1:1"
    kb_id, generation = _new_kb(), _new_generation()
    storage = _make_storage(dead_url, prefix, kb_id, generation, "chunks", CHUNK_META)

    with pytest.raises(ResponseHandlingException) as error_info:
        await storage.initialize()
    assert str(error_info.value), "error message must not be empty"
    assert storage._client is not None  # client handle created but unusable

    # A second attempt fails the same way (no silent recovery / Nano fallback)
    with pytest.raises(ResponseHandlingException):
        await storage.initialize()

    # The storage adapter is still the Physical Qdrant backend: no fallback type.
    assert type(storage).__name__ == "PhysicalQdrantVectorDBStorage"


@pytest.mark.asyncio
async def test_verify_generation_reports_missing_collection_clearly(
    test_env: dict[str, str],
) -> None:
    prefix, url = test_env["prefix"], test_env["url"]
    kb_id, generation = _new_kb(), _new_generation()
    service = QdrantCollectionService(_qdrant_settings(url, prefix, kb_id, generation))
    with pytest.raises(RuntimeError, match="collection is missing"):
        await service.verify_generation()

