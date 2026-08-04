from industrial_rag.answer_grounding import AnswerPoint
from industrial_rag.api import CitationResponse, _claims_for_result
from industrial_rag.citation_formatter import Citation
from industrial_rag.claim_citation_pruning import (
    prune_claim_citations,
    prune_claims_and_citations,
    prune_supported_claims_and_citations,
)
from industrial_rag.claim_support_matcher import match_claim_support
from industrial_rag.lightrag_service import QueryResult


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


def test_support_matcher_accepts_only_identity_resolved_child_evidence() -> None:
    result = match_claim_support(
        {"claim_id": "P1", "text": "压力为5 MPa", "evidence_ids": ["E1"]},
        {
            "E1": {
                "evidence_id": "E1",
                "citation_id": "cite_1",
                "chunk_id": "c1",
                "generation_id": "g1",
                "text": "压力为5 MPa",
                "is_child": True,
            }
        },
        expected_generation_id="g1",
    )

    assert result.supported is True
    assert result.valid_evidence_ids == ("E1",)
    assert result.invalid_evidence_ids == ()


def test_support_matcher_rejects_unknown_wrong_generation_and_parent_evidence() -> None:
    registry = {
        "E_WRONG": {
            "evidence_id": "E_WRONG",
            "citation_id": "cite_wrong",
            "chunk_id": "c-wrong",
            "generation_id": "g2",
            "text": "压力为5 MPa",
            "is_child": True,
        },
        "E_PARENT": {
            "evidence_id": "E_PARENT",
            "citation_id": "cite_parent",
            "chunk_id": "parent-1",
            "generation_id": "g1",
            "text": "压力为5 MPa",
            "is_child": "false",
        },
    }

    result = match_claim_support(
        {
            "claim_id": "P1",
            "text": "压力为5 MPa",
            "evidence_ids": ["UNKNOWN", "E_WRONG", "E_PARENT"],
        },
        registry,
        expected_generation_id="g1",
    )

    assert result.supported is False
    assert result.valid_evidence_ids == ()
    assert result.invalid_evidence_ids == ("UNKNOWN", "E_WRONG", "E_PARENT")
    assert result.reason_codes == (
        "unknown_evidence_id",
        "wrong_generation",
        "parent_not_public_citation",
    )


def test_pruning_removes_unsupported_claims_independently_and_keeps_minimal_citations() -> None:
    registry = {
        "E1": {
            "evidence_id": "E1",
            "citation_id": "cite_1",
            "chunk_id": "c1",
            "generation_id": "g1",
            "text": "压力为5 MPa",
            "is_child": True,
        },
        "E2": {
            "evidence_id": "E2",
            "citation_id": "cite_2",
            "chunk_id": "c2",
            "generation_id": "g1",
            "text": "允许超过5 MPa",
            "is_child": True,
        },
    }
    claims, metrics = prune_supported_claims_and_citations(
        [
            {"claim_id": "P1", "text": "压力为5 MPa", "evidence_ids": ["E1", "E2"], "citation_ids": ["cite_1", "cite_2"]},
            {"claim_id": "P2", "text": "不得超过5 MPa", "evidence_ids": ["E2"], "citation_ids": ["cite_2"]},
        ],
        [_cit("cite_1", "E1", "c1"), _cit("cite_2", "E2", "c2")],
        evidence_registry=registry,
        expected_generation_id="g1",
    )

    assert claims == [
        {"claim_id": "P1", "text": "压力为5 MPa", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]}
    ]
    assert metrics["unsupported_claim_count_after"] == 1
    assert metrics["removed_unsupported_claim_ids"] == ["P2"]


def test_runtime_pruning_uses_internal_child_evidence_only() -> None:
    result = QueryResult(
        "压力为5 MPa\n不得超过5 MPa",
        (Citation("manual.pdf", 1, "c1"), Citation("manual.pdf", 2, "parent")),
        "naive",
        answer_points=(
            AnswerPoint("P1", "压力为5 MPa", ("E1",), "supported"),
            AnswerPoint("P2", "不得超过5 MPa", ("E2",), "supported"),
        ),
        evidence_context=(
            {"evidence_id": "E1", "chunk_id": "c1", "generation_id": "g1", "text": "压力为5 MPa", "is_child": True},
            {"evidence_id": "E2", "chunk_id": "parent", "generation_id": "g1", "text": "不得超过5 MPa", "is_child": False, "context_role": "context_only"},
        ),
    )
    citations = [
        CitationResponse(citation_id="cite_1", document_name="manual.pdf", page=1, chunk_id="c1", generation_id="g1", evidence_id="E1"),
        CitationResponse(citation_id="cite_2", document_name="manual.pdf", page=2, chunk_id="parent", generation_id="g1", evidence_id="E2"),
    ]
    claims = _claims_for_result(
        result, citations, allow_legacy_fallback=False, claim_citation_pruning_enabled=True,
        evidence_context=result.evidence_context, expected_generation_id="g1",
    )
    assert [claim.claim_id for claim in claims] == ["P1"]
    assert claims[0].citation_ids == ["cite_1"]
