from industrial_rag.claim_citation_pruning import (
    prune_claim_citations,
    prune_claims_and_citations,
)


def _cit(cid: str, eid: str, chunk: str, generation: str = "g1") -> dict[str, str]:
    return {"citation_id": cid, "evidence_id": eid, "chunk_id": chunk, "generation_id": generation}


def test_prunes_overcitation_to_declared_evidence_in_response_order() -> None:
    citations = [_cit("cite_1", "E1", "c1"), _cit("cite_2", "E2", "c2"), _cit("cite_3", "E3", "c3")]
    result = prune_claim_citations(
        {"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1", "cite_2", "cite_3"]},
        citations,
    )
    assert result.claim["citation_ids"] == ["cite_1"]
    assert result.removed_citation_ids == ("cite_2", "cite_3")
    assert result.reason == "overcitation_pruned"


def test_shared_evidence_is_preserved_for_each_claim() -> None:
    citations = [_cit("cite_1", "E1", "c1"), _cit("cite_2", "E2", "c2")]
    claims, metrics = prune_claims_and_citations(
        [
            {"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1", "cite_2"]},
            {"claim_id": "P2", "evidence_ids": ["E1", "E2"], "citation_ids": ["cite_1", "cite_2"]},
        ],
        citations,
    )
    assert claims[0]["citation_ids"] == ["cite_1"]
    assert claims[1]["citation_ids"] == ["cite_1", "cite_2"]
    assert metrics["overcitation_claim_count_before"] == 1
    # E1 remains counted as covered for both claims; pruning is not refusal.
    assert metrics["unsupported_claim_count_after"] == 0


def test_unknown_evidence_never_falls_back_to_all_citations() -> None:
    result = prune_claim_citations(
        {"claim_id": "P1", "evidence_ids": ["UNKNOWN"], "citation_ids": ["cite_1", "cite_2"]},
        [_cit("cite_1", "E1", "c1"), _cit("cite_2", "E2", "c2")],
    )
    assert result.claim["citation_ids"] == []
    assert result.unresolved_evidence_ids == ("UNKNOWN",)
    assert result.reason == "no_identity_resolved_citations"


def test_cross_generation_citation_is_rejected_without_identity_mutation() -> None:
    evidence = {"E1": {"evidence_id": "E1", "chunk_id": "c1", "generation_id": "g1"}}
    result = prune_claim_citations(
        {"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]},
        [_cit("cite_1", "E1", "c1", "g2")],
        evidence_registry=evidence,
        expected_generation_id="g1",
    )
    assert result.claim["evidence_ids"] == ["E1"]
    assert result.claim["citation_ids"] == []


def test_same_chunk_does_not_generate_multiple_public_citations() -> None:
    citations = [_cit("cite_1", "E1", "c1"), _cit("cite_duplicate", "E2", "c1")]
    result = prune_claim_citations(
        {"claim_id": "P1", "evidence_ids": ["E1", "E2"], "citation_ids": ["cite_1", "cite_duplicate"]},
        citations,
    )
    assert result.claim["citation_ids"] == ["cite_1"]
