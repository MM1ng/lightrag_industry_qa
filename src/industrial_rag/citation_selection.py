"""Deterministic minimal citation selection for offline citation remapping.

The selector only operates on citation candidates already present in the
answer chain.  It does not retrieve, rerank, regenerate, or change answer
text.  A caller must provide the evidence-to-claim support mapping produced
by its existing grounding/evaluation boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CitationSelectionResult:
    """Minimal selected citations and auditable removal information."""

    claim_citation_ids: dict[str, tuple[str, ...]]
    retained_citation_ids: tuple[str, ...]
    removed_citation_ids: tuple[str, ...]
    supporting_removed: tuple[str, ...]
    non_supporting_removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeCitationSelectionResult:
    """Runtime top-level citations selected from final claim evidence."""

    claims: tuple[dict[str, Any], ...]
    citations: tuple[dict[str, Any], ...]
    retained_evidence_ids: tuple[str, ...]
    missing_evidence_ids: tuple[str, ...]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _unique(values: Sequence[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def select_minimal_supporting_citations(
    *,
    claims: Sequence[Mapping[str, Any]],
    citations: Sequence[Mapping[str, Any]],
) -> CitationSelectionResult:
    """Select only candidate citations explicitly supporting at least one claim.

    ``supporting_citation_ids`` is intentionally supplied by the existing
    grounding/evaluation mapping.  This function does not infer support from
    query overlap and therefore cannot silently turn context relevance into a
    citation.  Parent/child duplicates are collapsed only when both entries
    are marked supporting for the same claim; otherwise the caller's direct
    support mapping is preserved.
    """

    citation_by_id = {
        _text(citation.get("citation_id")): citation
        for citation in citations
        if _text(citation.get("citation_id"))
    }
    response_order = tuple(cid for cid in citation_by_id if cid)
    claim_citation_ids: dict[str, tuple[str, ...]] = {}
    supporting_union: set[str] = set()
    candidate_union: set[str] = set()

    for claim in claims:
        claim_id = _text(claim.get("claim_id"))
        if not claim_id:
            continue
        candidates = set(_unique(claim.get("candidate_citation_ids", ()))) & set(citation_by_id)
        supporting = set(_unique(claim.get("supporting_citation_ids", ()))) & candidates
        candidate_union.update(candidates)
        supporting_union.update(supporting)

        # If both parent and child are explicitly supporting the same claim,
        # keep the direct child.  If only the parent is supporting, retain it.
        child_ids = {
            cid
            for cid in supporting
            if _text(citation_by_id[cid].get("parent_chunk_id"))
            and _text(citation_by_id[cid].get("parent_chunk_id"))
            in {
                _text(citation_by_id[parent_id].get("chunk_id"))
                for parent_id in supporting
            }
        }
        parent_chunk_ids = {
            _text(citation_by_id[cid].get("parent_chunk_id"))
            for cid in child_ids
            if _text(citation_by_id[cid].get("parent_chunk_id"))
        }
        preferred = {
            cid
            for cid in supporting
            if not (
                _text(citation_by_id[cid].get("chunk_id")) in parent_chunk_ids
                and cid not in child_ids
            )
        }

        selected: list[str] = []
        seen_chunks: set[str] = set()
        for citation_id in response_order:
            if citation_id not in preferred:
                continue
            chunk_id = _text(citation_by_id[citation_id].get("chunk_id"))
            if chunk_id and chunk_id in seen_chunks:
                continue
            selected.append(citation_id)
            if chunk_id:
                seen_chunks.add(chunk_id)
        claim_citation_ids[claim_id] = tuple(selected)

    retained: list[str] = []
    retained_chunks: set[str] = set()
    for citation_id in response_order:
        if not any(citation_id in ids for ids in claim_citation_ids.values()):
            continue
        chunk_id = _text(citation_by_id[citation_id].get("chunk_id"))
        if chunk_id and chunk_id in retained_chunks:
            continue
        retained.append(citation_id)
        if chunk_id:
            retained_chunks.add(chunk_id)

    removed = tuple(cid for cid in response_order if cid not in retained)
    supporting_removed = tuple(cid for cid in response_order if cid in supporting_union and cid not in retained)
    non_supporting_removed = tuple(
        cid for cid in response_order if cid not in supporting_union and (cid in candidate_union or cid not in retained)
    )
    return CitationSelectionResult(
        claim_citation_ids=claim_citation_ids,
        retained_citation_ids=tuple(retained),
        removed_citation_ids=removed,
        supporting_removed=supporting_removed,
        non_supporting_removed=non_supporting_removed,
    )


def select_runtime_citations(
    *,
    claims: Sequence[Mapping[str, Any]],
    response_evidence: Sequence[Mapping[str, Any]],
) -> RuntimeCitationSelectionResult:
    """Select top-level citations from final runtime claim evidence only.

    The selector intentionally accepts only runtime claim evidence IDs and
    response evidence metadata.  It does not infer support from query overlap,
    context membership, or any external evaluation artifact.
    """

    claim_copies = tuple(dict(claim) for claim in claims)
    requested_ids: list[str] = []
    requested_seen: set[str] = set()
    for claim in claims:
        for evidence_id in _unique(claim.get("evidence_ids", ())):
            if evidence_id not in requested_seen:
                requested_seen.add(evidence_id)
                requested_ids.append(evidence_id)

    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for evidence in response_evidence:
        evidence_id = _text(evidence.get("evidence_id"))
        citation_id = _text(evidence.get("citation_id"))
        if evidence_id and citation_id and evidence_id not in evidence_by_id:
            evidence_by_id[evidence_id] = evidence

    missing = tuple(evidence_id for evidence_id in requested_ids if evidence_id not in evidence_by_id)
    retained_evidence_ids = tuple(evidence_id for evidence_id in requested_ids if evidence_id in evidence_by_id)
    retained_set = set(retained_evidence_ids)

    citations: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    for evidence in response_evidence:
        evidence_id = _text(evidence.get("evidence_id"))
        if evidence_id not in retained_set:
            continue
        chunk_id = _text(evidence.get("chunk_id"))
        if chunk_id and chunk_id in seen_chunks:
            continue
        citations.append(dict(evidence))
        if chunk_id:
            seen_chunks.add(chunk_id)

    return RuntimeCitationSelectionResult(
        claims=claim_copies,
        citations=tuple(citations),
        retained_evidence_ids=tuple(_text(item.get("evidence_id")) for item in citations),
        missing_evidence_ids=missing,
    )


__all__ = [
    "CitationSelectionResult",
    "RuntimeCitationSelectionResult",
    "select_minimal_supporting_citations",
    "select_runtime_citations",
]
