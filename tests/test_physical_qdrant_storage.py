"""Fake-client unit tests for PhysicalQdrantVectorDBStorage.

These tests exercise the storage adapter against an in-memory fake Qdrant
client so the collection-validation, payload, and point-id contracts are
locked without depending on a running Qdrant server. Real-Qdrant integration
lives in the opt-in ``IRA_QDRANT_INTEGRATION=1`` suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from industrial_rag.physical_qdrant_storage import (
    PhysicalQdrantVectorDBStorage,
    _point_id,
)

PREFIX = "ira_unit"
KB_ID = "a" * 32
GENERATION = "g12345678abcd"
COLLECTION = f"{PREFIX}_kb_{KB_ID}_{GENERATION}_chunks"


class FakeEmbedding:
    embedding_dim = 1024

    async def __call__(
        self, texts: list[str], context: str = "document", _priority: int | None = None
    ) -> list[list[float]]:
        return [[0.01 * (index + 1)] * self.embedding_dim for index in range(len(texts))]


class _Vectors:
    def __init__(self, size: int, distance: str) -> None:
        self.size = size
        self.distance = distance


class _Params:
    def __init__(self, size: int, distance: str) -> None:
        self.vectors = _Vectors(size, distance)


class _Config:
    def __init__(self, size: int, distance: str) -> None:
        self.params = _Params(size, distance)


class CollectionInfo:
    def __init__(self, size: int, distance: str, status: str = "green") -> None:
        self.config = _Config(size, distance)
        self.status = status


@dataclass
class _Point:
    payload: dict[str, Any] | None
    score: float = 0.8
    vector: list[float] | None = None


class _QueryResponse:
    def __init__(self, points: list[_Point]) -> None:
        self.points = points


class FakeQdrantClient:
    """In-memory stand-in for the AsyncQdrantClient surface the storage uses."""

    def __init__(
        self,
        *,
        collections: dict[str, CollectionInfo] | None = None,
        fail_get_collection: Exception | None = None,
    ) -> None:
        self.collections: dict[str, CollectionInfo] = dict(collections or {})
        self.fail_get_collection = fail_get_collection
        self.created: list[tuple[str, Any]] = []
        self.deleted: list[str] = []
        self.upserted: list[tuple[str, list[Any]]] = []
        self.deleted_points: list[tuple[str, Any]] = []
        self.retrieved: list[tuple[str, list[str], bool, bool]] = []
        self.closed = False
        self._points: dict[str, list[_Point]] = {name: [] for name in self.collections}

    async def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    async def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.created.append((collection_name, vectors_config))
        self.collections[collection_name] = CollectionInfo(
            vectors_config.size, vectors_config.distance
        )
        self._points.setdefault(collection_name, [])

    async def get_collection(self, collection_name: str) -> CollectionInfo:
        if self.fail_get_collection is not None:
            raise self.fail_get_collection
        return self.collections[collection_name]

    async def query_points(self, **kwargs: Any) -> _QueryResponse:
        collection_name = kwargs["collection_name"]
        return _QueryResponse(list(self._points.get(collection_name, [])))

    async def upsert(self, collection_name: str, points: list[Any], wait: bool = True) -> None:
        self.upserted.append((collection_name, points))
        self._points.setdefault(collection_name, []).extend(
            _Point(payload=point.payload, score=0.9) for point in points
        )

    async def delete(
        self, collection_name: str, selector: Any, wait: bool = True
    ) -> None:
        self.deleted_points.append((collection_name, selector))

    async def retrieve(
        self, collection_name: str, ids: list[str], with_payload: bool, with_vectors: bool = False
    ) -> list[_Point]:
        self.retrieved.append((collection_name, ids, with_payload, with_vectors))
        by_id = {
            _point_id(str(point.payload["id"])): point
            for point in self._points.get(collection_name, [])
            if point.payload and "id" in point.payload
        }
        return [by_id[point_id] for point_id in ids if point_id in by_id]

    async def delete_collection(self, collection_name: str) -> None:
        self.deleted.append(collection_name)
        self.collections.pop(collection_name, None)

    async def close(self) -> None:
        self.closed = True


def _make_storage(
    fake: FakeQdrantClient | None = None,
    *,
    threshold: float | None = 0.2,
    namespace: str = "chunks",
    meta_fields: set[str] | None = None,
    monkeypatch: Any = None,
) -> PhysicalQdrantVectorDBStorage:
    kwargs: dict[str, Any] = {
        "qdrant_collection_prefix": PREFIX,
        "qdrant_generation": GENERATION,
        "qdrant_kb_id": KB_ID,
        "qdrant_url": "http://127.0.0.1:6333",
    }
    if threshold is not None:
        kwargs["cosine_better_than_threshold"] = threshold
    storage = PhysicalQdrantVectorDBStorage(
        namespace=namespace,
        workspace="",
        global_config={"vector_db_storage_cls_kwargs": kwargs},
        embedding_func=FakeEmbedding(),
        meta_fields=meta_fields or {"full_doc_id", "content", "file_path"},
    )
    if monkeypatch is not None:
        import industrial_rag.physical_qdrant_storage as module

        monkeypatch.setattr(module, "AsyncQdrantClient", lambda **_: fake)
    return storage


@pytest.mark.asyncio
async def test_initialize_creates_missing_collection_with_expected_config(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch)

    await storage.initialize()

    assert fake.created == [(COLLECTION, storage._client.created[0][1])]
    name, vectors_config = fake.created[0]
    assert name == COLLECTION
    assert vectors_config.size == 1024
    assert vectors_config.distance == "Cosine"
    assert fake.closed is False


@pytest.mark.asyncio
async def test_initialize_reuses_matching_existing_collection(monkeypatch: Any) -> None:
    fake = FakeQdrantClient(collections={COLLECTION: CollectionInfo(1024, "Cosine")})
    storage = _make_storage(fake, monkeypatch=monkeypatch)

    await storage.initialize()

    assert fake.created == []
    assert fake.deleted == []
    assert fake.closed is False


@pytest.mark.asyncio
async def test_initialize_rejects_existing_collection_with_wrong_dimension(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient(collections={COLLECTION: CollectionInfo(512, "Cosine")})
    storage = _make_storage(fake, monkeypatch=monkeypatch)

    with pytest.raises(RuntimeError, match="size=512 distance=Cosine; expected size=1024"):
        await storage.initialize()

    assert fake.created == []
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_initialize_rejects_existing_collection_with_wrong_distance(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient(collections={COLLECTION: CollectionInfo(1024, "Euclid")})
    storage = _make_storage(fake, monkeypatch=monkeypatch)

    with pytest.raises(RuntimeError, match="distance=Euclid"):
        await storage.initialize()

    assert fake.created == []
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_initialize_raises_clear_error_when_collection_config_unreadable(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient(
        collections={COLLECTION: CollectionInfo(1024, "Cosine")},
        fail_get_collection=RuntimeError("connection refused"),
    )
    storage = _make_storage(fake, monkeypatch=monkeypatch)

    with pytest.raises(RuntimeError, match="exists but its config cannot be read"):
        await storage.initialize()


def test_cosine_threshold_is_taken_from_framework_kwargs() -> None:
    storage = _make_storage(threshold=0.45)

    assert storage.cosine_better_than_threshold == 0.45


def test_cosine_threshold_defaults_when_kwargs_missing() -> None:
    storage = PhysicalQdrantVectorDBStorage(
        namespace="chunks",
        workspace="",
        global_config={
            "vector_db_storage_cls_kwargs": {
                "qdrant_collection_prefix": PREFIX,
                "qdrant_generation": GENERATION,
                "qdrant_kb_id": KB_ID,
                "qdrant_url": "http://127.0.0.1:6333",
            }
        },
        embedding_func=FakeEmbedding(),
        meta_fields={"full_doc_id", "content", "file_path"},
    )

    assert storage.cosine_better_than_threshold == 0.2


@pytest.mark.asyncio
async def test_upsert_keeps_only_meta_fields_content_and_stable_point_ids(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch)
    await storage.initialize()

    await storage.upsert(
        {
            "chunk-1": {"content": "泵轴温度检查。", "full_doc_id": "doc-a", "extra": "ignored"},
            "chunk-2": {"content": "密封冷却。", "full_doc_id": "doc-a"},
        }
    )

    assert len(fake.upserted) == 1
    _, points = fake.upserted[0]
    assert len(points) == 2
    first = points[0]
    assert first.id == _point_id("chunk-1")
    # Phase 9: provenance fields (kb_id/generation) are part of the payload
    # contract so every point is traceable to its KB and generation.
    assert set(first.payload) == {"id", "content", "full_doc_id", "kb_id", "generation"}
    assert first.payload["id"] == "chunk-1"
    assert first.payload["content"] == "泵轴温度检查。"
    assert first.payload["full_doc_id"] == "doc-a"
    assert first.payload["kb_id"] == KB_ID
    assert first.payload["generation"] == GENERATION
    assert len(first.vector) == 1024


@pytest.mark.asyncio
async def test_query_returns_payload_with_distance_score(monkeypatch: Any) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch)
    await storage.initialize()
    await storage.upsert(
        {"chunk-1": {"content": "泵轴温度检查。", "full_doc_id": "doc-a"}}
    )

    results = await storage.query("泵轴温度", top_k=1)

    assert len(results) == 1
    assert results[0]["id"] == "chunk-1"
    assert results[0]["content"] == "泵轴温度检查。"
    assert results[0]["distance"] == 0.9


@pytest.mark.asyncio
async def test_get_by_ids_returns_in_input_order_with_missing_none(
    monkeypatch: Any,
) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch)
    await storage.initialize()
    await storage.upsert({"chunk-1": {"content": "A", "full_doc_id": "doc-a"}})

    results = await storage.get_by_ids(["chunk-1", "missing"])

    assert [item["id"] if item else None for item in results] == ["chunk-1", None]


@pytest.mark.asyncio
async def test_delete_entity_relation_uses_src_tgt_filter(monkeypatch: Any) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch, namespace="relationships")
    await storage.initialize()

    await storage.delete_entity_relation("泵")

    assert len(fake.deleted_points) == 1
    collection, selector = fake.deleted_points[0]
    assert collection == COLLECTION.replace("_chunks", "_relationships")
    assert selector.should is not None
    keys = [condition.key for condition in selector.should]
    assert keys == ["src_id", "tgt_id"]


@pytest.mark.asyncio
async def test_drop_deletes_exact_registered_collection(monkeypatch: Any) -> None:
    fake = FakeQdrantClient(collections={COLLECTION: CollectionInfo(1024, "Cosine")})
    storage = _make_storage(fake, monkeypatch=monkeypatch)
    await storage.initialize()

    result = await storage.drop()

    assert result["status"] == "success"
    assert fake.deleted == [COLLECTION]


@pytest.mark.asyncio
async def test_finalize_closes_client(monkeypatch: Any) -> None:
    fake = FakeQdrantClient()
    storage = _make_storage(fake, monkeypatch=monkeypatch)
    await storage.initialize()

    await storage.finalize()

    assert fake.closed is True
    assert storage._client is None
