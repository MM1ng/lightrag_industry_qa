"""Lifecycle-safe Qdrant collection operations for KB shadow generations."""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from industrial_rag.config import Settings
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend


@dataclass(frozen=True, slots=True)
class QdrantCollectionService:
    """Operate only on the exact collections resolved for one KB generation."""

    settings: Settings

    def _require_qdrant(self) -> None:
        if self.settings.vector_backend is not VectorBackend.qdrant:
            raise RuntimeError("Qdrant collection operation requires the Qdrant backend")
        if not self.settings.qdrant_url or not self.settings.qdrant_kb_id:
            raise RuntimeError("Qdrant KB settings are incomplete")

    def names(self) -> dict[str, str]:
        self._require_qdrant()
        if self.settings.qdrant_generation is None:
            raise RuntimeError("Qdrant generation is required")
        return CollectionNameResolver(self.settings.qdrant_collection_prefix).names_for(
            kb_id=self.settings.qdrant_kb_id,
            generation=self.settings.qdrant_generation,
        )

    def _client(self) -> AsyncQdrantClient:
        self._require_qdrant()
        return AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=10,
        )

    async def delete_generation(self) -> None:
        """Delete only exact, generated collection names and verify they are gone."""
        client = self._client()
        try:
            for name in self.names().values():
                if await client.collection_exists(name):
                    await client.delete_collection(name)
                if await client.collection_exists(name):
                    raise RuntimeError(f"Qdrant collection cleanup was not durable: {name}")
        finally:
            await client.close()

    async def verify_generation(
        self,
        *,
        expected_chunks: int | None = None,
        require_chunks: bool = True,
    ) -> int:
        """Verify exact namespace collections and the expected chunks point count."""
        client = self._client()
        try:
            names = self.names()
            for name in names.values():
                if not await client.collection_exists(name):
                    raise RuntimeError(f"Qdrant collection is missing: {name}")
            count = (await client.count(names["chunks"], exact=True)).count
            minimum = expected_chunks if expected_chunks is not None else int(require_chunks)
            if count < minimum:
                raise RuntimeError(
                    f"Qdrant chunks collection has {count} points; expected at least {minimum}"
                )
            return count
        finally:
            await client.close()
