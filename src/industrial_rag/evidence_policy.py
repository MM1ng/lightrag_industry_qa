"""Deterministic trust policy for structured LightRAG evidence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from industrial_rag.citation_formatter import Citation, collect_citations

DOCUMENT_ALIASES = {
    "2196-ANSI-Manual-Chinese.pdf": frozenset({"2196", "summit", "2196-ansi-manual-chinese.pdf"}),
    "t1739cn.pdf": frozenset({"desmi", "t1739", "t1739cn.pdf"}),
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-_/.][a-z0-9]+)*|[\u3400-\u9fff]+")
_CJK_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]+")
_SOURCE_HEADER_PATTERN = re.compile(r"\[\[INDUSTRIAL_RAG_SOURCE\b[^\]]*\]\]")
_PROVENANCE_LINE_PATTERN = re.compile(r"(?m)^\[来源：[^\r\n]*\][ \t]*\r?\n?")
_MAX_SELECTED = 3
_CJK_PHRASE_LENGTH = 4
_CJK_IGNORED_PATTERN = re.compile(
    "|".join(
        re.escape(value)
        for value in (
            "设备维护",
            "注意事项",
            "为什么",
            "怎么办",
            "请问",
            "如何",
            "哪些",
            "哪个",
            "是否",
            "相关",
            "进行",
            "要求",
            "怎么",
            "什么",
            "问题",
            "内容",
            "情况",
            "说明",
            "有关",
            "的",
            "了",
            "吗",
            "呢",
        )
    )
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "how",
        "is",
        "or",
        "please",
        "the",
        "what",
        "which",
        "了",
        "什么",
        "哪些",
        "哪个",
        "吗",
        "呢",
        "如何",
        "是否",
        "的",
        "为什么",
        "怎么",
        "请问",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    citation: Citation
    text: str
    rank: int


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    allowed: bool
    routed_document: str | None
    selected: tuple[EvidenceCandidate, ...]


def select_evidence(question: str, payload: object, *, limit: int = 3) -> EvidenceDecision:
    """Return traceable candidates that meet deterministic routing and overlap gates."""
    question_tokens = _tokens(question)
    matched_documents = _matched_documents(question_tokens)
    routed_document = next(iter(matched_documents)) if len(matched_documents) == 1 else None
    candidates = _extract_candidates(payload)
    if matched_documents:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.citation.source_file in matched_documents
        ]
    scored = [
        (len(question_tokens & _tokens(candidate.text)), candidate) for candidate in candidates
    ]
    ranked = sorted(scored, key=lambda item: (-item[0], item[1].rank))
    selected = tuple(candidate for overlap, candidate in ranked if overlap >= 2)[
        : min(max(limit, 0), _MAX_SELECTED)
    ]
    if not selected:
        return EvidenceDecision(False, None, ())
    return EvidenceDecision(True, routed_document, selected)


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: set[str] = set()
    for token in _TOKEN_PATTERN.findall(normalized):
        if _CJK_TOKEN_PATTERN.fullmatch(token):
            tokens.update(_cjk_phrase_tokens(token))
            continue
        if token in _STOPWORDS:
            continue
        tokens.add(token)
    return frozenset(tokens)


def _matched_documents(tokens: frozenset[str]) -> frozenset[str]:
    filename_matches = frozenset(
        document for document in DOCUMENT_ALIASES if document.casefold() in tokens
    )
    if filename_matches:
        return filename_matches
    return frozenset(document for document, aliases in DOCUMENT_ALIASES.items() if tokens & aliases)


def _extract_candidates(payload: object) -> list[EvidenceCandidate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    data = payload["data"]
    candidates: list[EvidenceCandidate] = []
    identity_indexes: dict[tuple[str, int, str], int] = {}
    for field in ("references", "chunks"):
        values = data.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            citations = collect_citations({"data": {"references": [], "chunks": [value]}})
            if not citations:
                continue
            citation = citations[0]
            candidate_text = _candidate_text(value.get("content"))
            identity = (citation.source_file, citation.page_number, citation.chunk_id)
            existing_index = identity_indexes.get(identity)
            if existing_index is None:
                identity_indexes[identity] = len(candidates)
                candidates.append(
                    EvidenceCandidate(
                        citation=citation,
                        text=candidate_text,
                        rank=len(candidates),
                    )
                )
            elif not candidates[existing_index].text.strip() and candidate_text.strip():
                existing = candidates[existing_index]
                candidates[existing_index] = EvidenceCandidate(
                    citation=existing.citation,
                    text=candidate_text,
                    rank=existing.rank,
                )
    return candidates


def _candidate_text(content: object) -> str:
    if not isinstance(content, str):
        return ""
    without_headers = _SOURCE_HEADER_PATTERN.sub("", content)
    return _PROVENANCE_LINE_PATTERN.sub("", without_headers).strip()


def _cjk_phrase_tokens(token: str) -> frozenset[str]:
    meaningful = _CJK_IGNORED_PATTERN.sub("", token)
    if not meaningful:
        return frozenset()
    if len(meaningful) < _CJK_PHRASE_LENGTH:
        return frozenset({meaningful})
    return frozenset(
        meaningful[index : index + _CJK_PHRASE_LENGTH]
        for index in range(len(meaningful) - _CJK_PHRASE_LENGTH + 1)
    )
