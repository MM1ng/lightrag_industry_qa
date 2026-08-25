"""Exact-ID hydration for offline Runtime evidence replay.

This module reads an existing chunk registry.  It never accepts a question,
performs similarity search, calls Qdrant search, or consults evaluation data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_FORBIDDEN_KEYS = frozenset(
    {
        "supporting_actual_chunk_ids",
        "expected_support_chunk_ids",
        "expected_evidence",
        "expected_answer",
        "golden",
        "validation",
        "holdout",
        "oracle",
        "evaluation",
    }
)


@dataclass(frozen=True, slots=True)
class HydratedChunk:
    chunk_id: str
    text: str
    hydration_status: str
    original_text_length: int
    hydrated_text_length: int
    truncated: bool
    hydration_source: str | None


class RuntimeChunkHydrator:
    def __init__(self, records: Mapping[str, tuple[str, str]]) -> None:
        self._records = dict(records)

    @classmethod
    def from_jsonl(cls, paths: Iterable[Path]) -> "RuntimeChunkHydrator":
        records: dict[str, tuple[str, str]] = {}
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError(f"chunk registry row must be an object: {path}:{line_number}")
                _reject_evaluation_labels(value)
                chunk_id = str(value.get("chunk_id") or value.get("id") or "").strip()
                content = str(value.get("content") or "")
                if not chunk_id or not content:
                    continue
                previous = records.get(chunk_id)
                current = (content, str(path))
                if previous is not None and previous[0] != content:
                    raise ValueError(f"duplicate chunk_id has different text: {chunk_id}")
                records.setdefault(chunk_id, current)
        return cls(records)

    @property
    def available_chunk_ids(self) -> frozenset[str]:
        return frozenset(self._records)

    def hydrate(
        self,
        chunk_ids: Iterable[str],
        *,
        max_text_chars: int | None = None,
    ) -> dict[str, HydratedChunk]:
        result: dict[str, HydratedChunk] = {}
        for raw_chunk_id in chunk_ids:
            chunk_id = str(raw_chunk_id).strip()
            if not chunk_id or chunk_id in result:
                continue
            record = self._records.get(chunk_id)
            if record is None:
                result[chunk_id] = HydratedChunk(
                    chunk_id=chunk_id,
                    text="",
                    hydration_status="missing",
                    original_text_length=0,
                    hydrated_text_length=0,
                    truncated=False,
                    hydration_source=None,
                )
                continue
            content, source = record
            hydrated = content
            truncated = False
            if max_text_chars is not None:
                if max_text_chars < 1:
                    raise ValueError("max_text_chars must be positive when provided")
                hydrated = content[:max_text_chars]
                truncated = len(hydrated) < len(content)
            result[chunk_id] = HydratedChunk(
                chunk_id=chunk_id,
                text=hydrated,
                hydration_status="hydrated",
                original_text_length=len(content),
                hydrated_text_length=len(hydrated),
                truncated=truncated,
                hydration_source=source,
            )
        return result


def _reject_evaluation_labels(value: Mapping[str, Any]) -> None:
    for key in value:
        normalized = str(key).casefold()
        if normalized in _FORBIDDEN_KEYS or any(
            marker in normalized for marker in ("golden", "holdout", "validation")
        ):
            raise ValueError("evaluation label is not allowed during hydration")


__all__ = ["HydratedChunk", "RuntimeChunkHydrator"]
