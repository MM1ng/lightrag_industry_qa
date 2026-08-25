"""Validated Qdrant backend selection and physical collection naming."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from industrial_rag.storage_layout import _validate_kb_id

QDRANT_VECTOR_NAMESPACES = ("chunks", "entities", "relationships")
_SAFE_COMPONENT = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SAFE_GENERATION = re.compile(r"^g[a-z0-9]{8,63}$")


class VectorBackend(enum.StrEnum):
    """Supported vector backends for a knowledge base."""

    nano = "nano"
    qdrant = "qdrant"


@dataclass(frozen=True, slots=True)
class CollectionNameResolver:
    """Resolve exact per-KB Qdrant collection names from trusted identifiers."""

    prefix: str

    def __post_init__(self) -> None:
        if not _SAFE_COMPONENT.fullmatch(self.prefix):
            raise ValueError("Qdrant collection prefix is invalid")

    def names_for(self, *, kb_id: str, generation: str) -> dict[str, str]:
        try:
            _validate_kb_id(kb_id)
        except ValueError as error:
            raise ValueError("knowledge base id is invalid") from error
        if not _SAFE_GENERATION.fullmatch(generation):
            raise ValueError("Qdrant generation is invalid")
        return {
            namespace: f"{self.prefix}_kb_{kb_id}_{generation}_{namespace}"
            for namespace in QDRANT_VECTOR_NAMESPACES
        }
