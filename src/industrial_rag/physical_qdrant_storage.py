"""Async Qdrant vector storage with one physical collection per KB generation."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

from lightrag.base import BaseVectorStorage
from lightrag.constants import DEFAULT_QUERY_PRIORITY
from qdrant_client import AsyncQdrantClient, models

from industrial_rag.vector_collections import CollectionNameResolver

_STORAGE_NAME = "PhysicalQdrantVectorDBStorage"


def _point_id(value: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(value.encode("utf-8")).digest()[:16]))


@dataclass
class PhysicalQdrantVectorDBStorage(BaseVectorStorage):
    """LightRAG adapter backed by a dedicated Qdrant collection per namespace."""

    def __post_init__(self) -> None:
        self._validate_embedding_func()
        options = self.global_config.get("vector_db_storage_cls_kwargs", {})
        prefix = str(options.get("qdrant_collection_prefix", "ira_qdrant"))
        generation = str(options.get("qdrant_generation", ""))
        kb_id = str(options.get("qdrant_kb_id", self.workspace))
        self._collection_name = CollectionNameResolver(prefix=prefix).names_for(
            kb_id=kb_id, generation=generation
        )[self.namespace]
        self._qdrant_url = str(options.get("qdrant_url", "")).strip()
        configured_api_key = options.get("qdrant_api_key")
        self._qdrant_api_key = (
            str(configured_api_key).strip() if configured_api_key is not None else None
        ) or None
        threshold = options.get("cosine_better_than_threshold")
        if threshold is not None:
            self.cosine_better_than_threshold = float(threshold)
        self._client: AsyncQdrantClient | None = None

    @property
    def collection_name(self) -> str:
        return self._collection_name

    async def initialize(self) -> None:
        url = self._qdrant_url or os.environ.get("QDRANT_URL", "").strip()
        if not url:
            raise RuntimeError("QDRANT_URL is required for the Qdrant vector backend")
        self._client = AsyncQdrantClient(
            url=url.rstrip("/"),
            api_key=self._qdrant_api_key or os.environ.get("QDRANT_API_KEY") or None,
        )
        if await self._client.collection_exists(self.collection_name):
            await self._validate_existing_collection()
            return
        await self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=self.embedding_func.embedding_dim,
                distance=models.Distance.COSINE,
            ),
        )

    async def _validate_existing_collection(self) -> None:
        """Reuse an existing collection only when its vector config matches.

        Qdrant collections cannot change dimension/distance after creation, so a
        mismatch means the registered collection was built for a different
        embedding contract. Fail loudly instead of silently reusing or deleting
        the mismatched collection.
        """
        client = self._require_client()
        try:
            info = await client.get_collection(self.collection_name)
        except Exception as error:
            raise RuntimeError(
                f"Qdrant collection '{self.collection_name}' exists but its config "
                f"cannot be read: {error}"
            ) from error
        params = info.config.params
        vectors = getattr(params, "vectors", None)
        size = getattr(vectors, "size", None)
        distance = getattr(vectors, "distance", None)
        expected_size = self.embedding_func.embedding_dim
        if size != expected_size or distance != models.Distance.COSINE:
            raise RuntimeError(
                f"Qdrant collection '{self.collection_name}' already exists with "
                f"size={size} distance={distance}; expected size={expected_size} "
                f"distance={models.Distance.COSINE}. Refusing to reuse a mismatched "
                "collection (only delete it after confirming it is a project collection)."
            )

    async def finalize(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _require_client(self) -> AsyncQdrantClient:
        if self._client is None:
            raise RuntimeError("Qdrant storage is not initialized")
        return self._client

    async def index_done_callback(self) -> None:
        return None

    async def drop(self) -> dict[str, str]:
        client = self._require_client()
        await client.delete_collection(self.collection_name)
        return {"status": "success", "message": "collection deleted"}

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        if query_embedding is None:
            embedding = (await self.embedding_func(
                [query], context="query", _priority=DEFAULT_QUERY_PRIORITY
            ))[0]
        else:
            embedding = query_embedding
        response = await self._require_client().query_points(
            collection_name=self.collection_name,
            query=embedding,
            limit=top_k,
            with_payload=True,
            score_threshold=self.cosine_better_than_threshold,
        )
        return [
            {**dict(point.payload or {}), "distance": point.score}
            for point in response.points
        ]

    async def upsert(self, data: dict[str, dict[str, Any]]) -> None:
        if not data:
            return
        ids = list(data)
        batch_size = max(
            1, int(self.global_config.get("embedding_batch_num", 10) or 10)
        )
        vectors: list[list[float]] = []
        contents = [str(data[doc_id]["content"]) for doc_id in ids]
        for start in range(0, len(contents), batch_size):
            vectors.extend(
                await self.embedding_func(
                    contents[start : start + batch_size], context="document"
                )
            )
        points = [
            models.PointStruct(
                id=_point_id(doc_id),
                vector=vector.tolist() if hasattr(vector, "tolist") else vector,
                payload={
                    "id": doc_id,
                    **{
                        key: value
                        for key, value in data[doc_id].items()
                        if key in self.meta_fields or key == "content"
                    },
                },
            )
            for doc_id, vector in zip(ids, vectors, strict=True)
        ]
        await self._require_client().upsert(self.collection_name, points, wait=True)

    async def delete(self, ids: list[str]) -> None:
        if ids:
            await self._require_client().delete(
                self.collection_name,
                models.PointIdsList(points=[_point_id(doc_id) for doc_id in ids]),
                wait=True,
            )

    async def delete_entity(self, entity_name: str) -> None:
        await self.delete([entity_name])

    async def delete_entity_relation(self, entity_name: str) -> None:
        relation_filter = models.Filter(
            should=[
                models.FieldCondition(
                    key="src_id", match=models.MatchValue(value=entity_name)
                ),
                models.FieldCondition(
                    key="tgt_id", match=models.MatchValue(value=entity_name)
                ),
            ]
        )
        await self._require_client().delete(self.collection_name, relation_filter, wait=True)

    async def get_by_id(self, id: str) -> dict[str, Any] | None:
        results = await self._require_client().retrieve(
            self.collection_name, ids=[_point_id(id)], with_payload=True
        )
        return dict(results[0].payload or {}) if results else None

    async def get_by_ids(self, ids: list[str]) -> list[dict[str, Any] | None]:
        if not ids:
            return []
        results = await self._require_client().retrieve(
            self.collection_name, ids=[_point_id(doc_id) for doc_id in ids], with_payload=True
        )
        found = {str(point.payload.get("id")): dict(point.payload or {}) for point in results if point.payload}
        return [found.get(doc_id) for doc_id in ids]

    async def get_vectors_by_ids(self, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        results = await self._require_client().retrieve(
            self.collection_name,
            ids=[_point_id(doc_id) for doc_id in ids],
            with_payload=True,
            with_vectors=True,
        )
        return {
            str(point.payload["id"]): list(point.vector)
            for point in results
            if point.payload and point.vector is not None and "id" in point.payload
        }
