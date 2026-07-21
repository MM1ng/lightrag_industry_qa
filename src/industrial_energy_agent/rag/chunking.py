"""Deterministic within-page text normalization and chunking."""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\r\n]+")
_BOUNDARIES = (
    "\n\n",
    "\n",
    "。",
    "\N{FULLWIDTH EXCLAMATION MARK}",
    "\N{FULLWIDTH QUESTION MARK}",
    ".",
    "!",
    "?",
    "\N{FULLWIDTH SEMICOLON}",
    ";",
)


class ChunkingConfig(BaseModel):
    """Versioned character boundary used before LightRAG ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_characters: int = Field(default=1800, ge=16, le=20_000)
    overlap_characters: int = Field(default=180, ge=0, le=5_000)

    @model_validator(mode="after")
    def require_bounded_overlap(self) -> ChunkingConfig:
        if self.overlap_characters >= self.max_characters:
            raise ValueError("overlap_characters must be less than max_characters")
        return self


class PageTextChunk(BaseModel):
    """Low-level chunk that can only belong to one physical page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    chunk_id: str = Field(min_length=1)


def normalize_text(value: str) -> str:
    """Normalize whitespace without collapsing paragraph boundaries."""

    normalized_lines: list[str] = []
    previous_blank = False
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _HORIZONTAL_WHITESPACE.sub(" ", raw_line).strip()
        if not line:
            if normalized_lines and not previous_blank:
                normalized_lines.append("")
            previous_blank = True
            continue
        normalized_lines.append(line)
        previous_blank = False
    while normalized_lines and not normalized_lines[-1]:
        normalized_lines.pop()
    return "\n".join(normalized_lines)


def _choose_end(text: str, start: int, proposed_end: int) -> int:
    if proposed_end >= len(text):
        return len(text)
    minimum_boundary = start + (proposed_end - start) // 2
    best = -1
    best_width = 0
    for boundary in _BOUNDARIES:
        position = text.rfind(boundary, minimum_boundary, proposed_end)
        if position >= minimum_boundary and position + len(boundary) > best + best_width:
            best = position
            best_width = len(boundary)
    return proposed_end if best < 0 else best + best_width


def chunk_page_text(
    text: str,
    *,
    doc_id: str,
    page_number: int,
    config: ChunkingConfig | None = None,
) -> tuple[PageTextChunk, ...]:
    """Split normalized text without ever crossing a physical PDF page."""

    if page_number < 1:
        raise ValueError("page_number must be one-based")
    if not doc_id.strip():
        raise ValueError("doc_id is required")
    normalized = normalize_text(text)
    if not normalized:
        return ()
    resolved = config or ChunkingConfig()
    chunks: list[PageTextChunk] = []
    start = 0
    while start < len(normalized):
        end = _choose_end(normalized, start, start + resolved.max_characters)
        chunk_text = normalized[start:end].strip()
        if chunk_text:
            ordinal = len(chunks) + 1
            text_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:8]
            chunks.append(
                PageTextChunk(
                    text=chunk_text,
                    page_number=page_number,
                    ordinal=ordinal,
                    chunk_id=f"{doc_id}:p{page_number}:c{ordinal}:{text_hash}",
                )
            )
        if end >= len(normalized):
            break
        next_start = end - resolved.overlap_characters
        start = max(start + 1, next_start)
    return tuple(chunks)
