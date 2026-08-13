import inspect

from industrial_rag import citation_selection
from industrial_rag.citation_selection import select_runtime_citations


def evidence(evidence_id: str, citation_id: str, chunk_id: str) -> dict[str, str]:
    return {
        "evidence_id": evidence_id,
        "citation_id": citation_id,
        "chunk_id": chunk_id,
        "document_name": "manual.pdf",
        "page": "1",
    }


def test_three_context_evidence_one_claim_keeps_one_runtime_citation() -> None:
    result = select_runtime_citations(
        claims=[{"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]}],
        response_evidence=[evidence("E1", "cite_1", "c1"), evidence("E2", "cite_2", "c2"), evidence("E3", "cite_3", "c3")],
    )
    assert [item["citation_id"] for item in result.citations] == ["cite_1"]


def test_two_claims_use_stable_union_of_their_evidence() -> None:
    result = select_runtime_citations(
        claims=[
            {"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]},
            {"claim_id": "P2", "evidence_ids": ["E2"], "citation_ids": ["cite_2"]},
        ],
        response_evidence=[evidence("E1", "cite_1", "c1"), evidence("E2", "cite_2", "c2"), evidence("E3", "cite_3", "c3")],
    )
    assert [item["citation_id"] for item in result.citations] == ["cite_1", "cite_2"]


def test_shared_evidence_is_emitted_once() -> None:
    result = select_runtime_citations(
        claims=[
            {"claim_id": "P1", "evidence_ids": ["E1"]},
            {"claim_id": "P2", "evidence_ids": ["E1"]},
        ],
        response_evidence=[evidence("E1", "cite_1", "c1")],
    )
    assert [item["citation_id"] for item in result.citations] == ["cite_1"]


def test_context_only_evidence_does_not_become_citation() -> None:
    result = select_runtime_citations(
        claims=[{"claim_id": "P1", "evidence_ids": ["E1"]}],
        response_evidence=[evidence("E1", "cite_1", "c1"), evidence("E2", "cite_2", "c2")],
    )
    assert [item["citation_id"] for item in result.citations] == ["cite_1"]


def test_response_evidence_order_is_preserved() -> None:
    result = select_runtime_citations(
        claims=[{"claim_id": "P1", "evidence_ids": ["E1", "E2"]}],
        response_evidence=[evidence("E2", "cite_2", "c2"), evidence("E1", "cite_1", "c1")],
    )
    assert [item["citation_id"] for item in result.citations] == ["cite_2", "cite_1"]


def test_claim_level_mapping_is_not_modified() -> None:
    claims = [{"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]}]
    result = select_runtime_citations(
        claims=claims,
        response_evidence=[evidence("E1", "cite_1", "c1"), evidence("E2", "cite_2", "c2")],
    )
    assert result.claims == tuple(claims)
    assert claims == [{"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["cite_1"]}]


def test_missing_evidence_id_is_ignored_without_fabricated_citation() -> None:
    result = select_runtime_citations(
        claims=[{"claim_id": "P1", "evidence_ids": ["MISSING"]}],
        response_evidence=[evidence("E1", "cite_1", "c1")],
    )
    assert result.citations == ()
    assert result.missing_evidence_ids == ("MISSING",)


def test_no_claim_evidence_does_not_fallback_to_all_context() -> None:
    result = select_runtime_citations(
        claims=[],
        response_evidence=[evidence("E1", "cite_1", "c1"), evidence("E2", "cite_2", "c2")],
    )
    assert result.citations == ()


def test_runtime_selector_has_no_oracle_input_or_evaluation_dependency() -> None:
    source = inspect.getsource(citation_selection)
    assert "supporting_actual_chunk_ids" not in source
    assert "expected_support_chunk_ids" not in source
    assert "evaluation/" not in source

