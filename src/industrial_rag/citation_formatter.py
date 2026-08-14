"""Encode and display citations from trusted chunk metadata, never model prose."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import quote, unquote

_PREFIX = "rag-source::"
_PAGE_SEPARATOR = "::page="
_CHUNK_SEPARATOR = "::chunk="
_HEADER_PATTERN = re.compile(
    r"\[\[INDUSTRIAL_RAG_SOURCE file=(?P<file>\S+) "
    r"page=(?P<page>\d+) chunk=(?P<chunk>\S+)\]\]"
)
_PROVENANCE_PAREN_PATTERN = re.compile(
    r"[（(]\s*(?:证据来源|依据来源|来源)\s*[:：]\s*[^\n）)]*[）)]"
)
_PROVENANCE_LABEL_PATTERN = re.compile(r"^\s*(?:证据来源|依据来源|来源)\s*[:：]\s*")
_PROVENANCE_TRAILING_LABEL_PATTERN = re.compile(r"(?:证据来源|依据来源|来源)\s*[:：]\s*$")
_SOURCE_MARKER_PATTERN = _HEADER_PATTERN
_SOURCE_METADATA_HINT_PATTERN = re.compile(
    r"(?i)(?:\.pdf\b|\.docx?\b|\.txt\b|file\s*=|document\s*=|page\s*=|chunk\s*=|generation\s*=|第\s*\d+\s*页|\d+\s*页)"
)
_FACTUAL_VERB_PATTERN = re.compile(
    r"(?:规定|应|必须|可以|要求|建议|旋转|控制|安装|检查|保持|为|是|包括|需要)"
)
_PROVENANCE_SENTENCE_PATTERN = re.compile(
    r"(?:以上(?:步骤|信息|内容)|答案|结论)?\s*(?:依据|证据)?(?:来源|来自|依据)"
)


@dataclass(frozen=True, slots=True)
class Citation:
    source_file: str
    page_number: int
    chunk_id: str

    def __post_init__(self) -> None:
        if not self.source_file or not self.chunk_id or self.page_number < 1:
            raise ValueError("citation requires a source file, positive page and chunk ID")

    @property
    def display(self) -> str:
        return format_citation(self)


def encode_source_ref(citation: Citation) -> str:
    return (
        f"{_PREFIX}{quote(citation.source_file, safe='')}"
        f"{_PAGE_SEPARATOR}{citation.page_number}"
        f"{_CHUNK_SEPARATOR}{quote(citation.chunk_id, safe='')}"
    )


def encode_chunk_header(citation: Citation) -> str:
    return (
        f"[[INDUSTRIAL_RAG_SOURCE file={quote(citation.source_file, safe='')} "
        f"page={citation.page_number} chunk={quote(citation.chunk_id, safe='')}]]"
    )


def _decode_chunk_header(value: str) -> Citation:
    match = _HEADER_PATTERN.search(value)
    if match is None:
        raise ValueError("chunk content has no industrial_rag source header")
    return Citation(
        unquote(match.group("file")),
        int(match.group("page")),
        unquote(match.group("chunk")),
    )


def decode_source_ref(value: str) -> Citation:
    if (
        not value.startswith(_PREFIX)
        or _PAGE_SEPARATOR not in value
        or _CHUNK_SEPARATOR not in value
    ):
        raise ValueError("not an industrial_rag source reference")
    encoded_file, remainder = value[len(_PREFIX) :].rsplit(_PAGE_SEPARATOR, 1)
    raw_page, encoded_chunk = remainder.split(_CHUNK_SEPARATOR, 1)
    try:
        page_number = int(raw_page)
    except ValueError as error:
        raise ValueError("source reference has an invalid page") from error
    return Citation(unquote(encoded_file), page_number, unquote(encoded_chunk))


def format_citation(citation: Citation) -> str:
    return f"[{citation.source_file}，第{citation.page_number}页]"


def strip_provenance_metadata(value: str) -> str:
    """Remove internal source metadata while preserving factual answer text."""

    cleaned = _SOURCE_MARKER_PATTERN.sub("", value)
    cleaned = _PROVENANCE_PAREN_PATTERN.sub("", cleaned)
    cleaned = _PROVENANCE_TRAILING_LABEL_PATTERN.sub("", cleaned)
    return cleaned.strip()


def is_provenance_only_fragment(value: str) -> bool:
    """Return whether a fragment only describes evidence provenance.

    A source label or source coordinates are metadata.  A sentence that also
    contains a factual predicate remains a semantic answer point.
    """

    cleaned = strip_provenance_metadata(value)
    if not cleaned:
        return True
    labelled = _PROVENANCE_LABEL_PATTERN.sub("", cleaned).strip(" \t-•:：,，;；()（）[]【】")
    if labelled != cleaned:
        cleaned = labelled
    if not cleaned:
        return True
    if _PROVENANCE_SENTENCE_PATTERN.search(cleaned) and _SOURCE_METADATA_HINT_PATTERN.search(cleaned):
        return not bool(_FACTUAL_VERB_PATTERN.search(cleaned))
    if _FACTUAL_VERB_PATTERN.search(cleaned):
        return False
    return bool(_SOURCE_METADATA_HINT_PATTERN.search(cleaned)) and not bool(
        re.search(r"[。！？!?]", cleaned)
    )


def collect_citations(payload: object) -> tuple[Citation, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return ()
    data = payload["data"]
    candidates: list[object] = []
    for field in ("references", "chunks"):
        values = data.get(field, [])
        if isinstance(values, list):
            candidates.extend(values)
    citations: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        citation: Citation | None = None
        file_path = candidate.get("file_path")
        if isinstance(file_path, str):
            with suppress(ValueError):
                citation = decode_source_ref(file_path)
        content = candidate.get("content")
        if citation is None and isinstance(content, str):
            with suppress(ValueError):
                citation = _decode_chunk_header(content)
        if citation is None:
            continue
        identity = (citation.source_file, citation.page_number)
        if identity not in seen:
            seen.add(identity)
            citations.append(citation)
    return tuple(citations)
