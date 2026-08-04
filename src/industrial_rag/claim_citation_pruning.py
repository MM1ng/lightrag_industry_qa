"""Deterministic claim-level citation pruning.

This module deliberately operates on plain mappings so it can be used by
offline replay and audit tools without importing the API or invoking a model.
The only citations a claim may retain are citations explicitly connected to
one of the claim's evidence IDs.  There is no "all citations" fallback.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CitationPruningResult:
    """A pruned claim plus deterministic diagnostics."""

    claim: dict[str, Any]
    removed_citation_ids: tuple[str, ...]
    unresolved_evidence_ids: tuple[str, ...]
    reason: str | None = None


def _as_id(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _citation_identity(citation: Mapping[str, Any]) -> tuple[str, str]:
    """Return stable identity, preferring public ID and falling back to chunk."""

    return (_as_id(citation.get("citation_id")), _as_id(citation.get("chunk_id")))


def _dedupe_ids(values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _as_id(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def prune_claim_citations(
    claim: Mapping[str, Any],
    citations: Sequence[Mapping[str, Any]],
    *,
    evidence_registry: Mapping[str, Mapping[str, Any]] | None = None,
    expected_generation_id: str | None = None,
) -> CitationPruningResult:
    """Prune one claim to its minimum identity-resolved citation set.

    Citation order follows the response citation order, making output stable
    even when a provider emits evidence IDs in a different order.  Unknown
    evidence IDs and citations from another generation are never mapped.
    """

    original_ids = _dedupe_ids(claim.get("citation_ids", ()))
    evidence_ids = _dedupe_ids(claim.get("evidence_ids", ()))
    by_evidence: dict[str, list[Mapping[str, Any]]] = {}
    for citation in citations:
        evidence_id = _as_id(citation.get("evidence_id"))
        if not evidence_id:
            continue
        if expected_generation_id and _as_id(citation.get("generation_id")) not in {
            "",
            expected_generation_id,
        }:
            continue
        by_evidence.setdefault(evidence_id, []).append(citation)

    unresolved = tuple(eid for eid in evidence_ids if eid not in by_evidence)
    allowed: set[tuple[str, str]] = set()
    for evidence_id in evidence_ids:
        registry_entry = (evidence_registry or {}).get(evidence_id)
        if registry_entry is not None and expected_generation_id:
            generation_id = _as_id(registry_entry.get("generation_id"))
            if generation_id and generation_id != expected_generation_id:
                continue
        for citation in by_evidence.get(evidence_id, ()):
            allowed.add(_citation_identity(citation))

    selected: list[str] = []
    selected_keys: set[tuple[str, str]] = set()
    selected_chunks: set[str] = set()
    # Iterate citations, rather than claim IDs, to preserve public ordering.
    for citation in citations:
        key = _citation_identity(citation)
        citation_id = key[0]
        chunk_id = key[1]
        if (
            not citation_id
            or key in selected_keys
            or key not in allowed
            or (chunk_id and chunk_id in selected_chunks)
        ):
            continue
        selected_keys.add(key)
        if chunk_id:
            selected_chunks.add(chunk_id)
        selected.append(citation_id)

    removed = tuple(citation_id for citation_id in original_ids if citation_id not in selected)
    output = dict(claim)
    output["citation_ids"] = selected
    output["evidence_ids"] = list(evidence_ids)
    reason: str | None = None
    if not evidence_ids:
        reason = "claim_has_no_evidence_ids"
    elif not selected:
        reason = "no_identity_resolved_citations"
    elif removed:
        reason = "overcitation_pruned"
    return CitationPruningResult(output, removed, unresolved, reason)


def prune_claims_and_citations(
    claims: Sequence[Mapping[str, Any]],
    citations: Sequence[Mapping[str, Any]],
    *,
    evidence_registry: Mapping[str, Mapping[str, Any]] | None = None,
    expected_generation_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prune all claims and return claims plus auditable aggregate metrics."""

    results = [
        prune_claim_citations(
            claim,
            citations,
            evidence_registry=evidence_registry,
            expected_generation_id=expected_generation_id,
        )
        for claim in claims
    ]
    before = sum(len(_dedupe_ids(claim.get("citation_ids", ()))) for claim in claims)
    after = sum(len(result.claim.get("citation_ids", ())) for result in results)
    over_before = sum(bool(result.removed_citation_ids) for result in results)
    unsupported = sum(not bool(result.claim.get("citation_ids")) for result in results)
    metrics = {
        "claim_count": len(results),
        "citation_edges_before": before,
        "citation_edges_after": after,
        "overcitation_claim_count_before": over_before,
        "overcitation_claim_count_after": 0,
        "unsupported_claim_count_after": unsupported,
        "unresolved_evidence_id_count": sum(len(result.unresolved_evidence_ids) for result in results),
        "citation_precision_improvement_possible": after < before,
        "stable_order": True,
    }
    return [result.claim for result in results], metrics


__all__ = ["CitationPruningResult", "prune_claim_citations", "prune_claims_and_citations"]
