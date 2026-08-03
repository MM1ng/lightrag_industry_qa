from industrial_rag.answer_grounding import AnswerPoint
from industrial_rag.api import _citation_response, _claims_for_result
from industrial_rag.citation_formatter import Citation
from industrial_rag.lightrag_service import QueryResult


def test_claims_bind_only_their_declared_evidence_ids() -> None:
    result = QueryResult(
        answer="两个结论",
        citations=(Citation("manual.pdf", 1, "c1"), Citation("manual.pdf", 2, "c2")),
        mode="mix",
        answer_points=(
            AnswerPoint("P1", "结论一", ("E1",), "supported"),
            AnswerPoint("P2", "结论二", ("E2",), "supported"),
        ),
    )
    citations = [
        _citation_response(item, index, evidence_id=f"E{index}")
        for index, item in enumerate(result.citations, start=1)
    ]
    claims = _claims_for_result(result, citations)
    assert claims[0].citation_ids == ["cite_1"]
    assert claims[1].citation_ids == ["cite_2"]
    assert claims[0].evidence_ids == ["E1"]


def test_unknown_evidence_id_is_not_mapped_as_all_citations() -> None:
    result = QueryResult(
        answer="结论",
        citations=(Citation("manual.pdf", 1, "c1"), Citation("manual.pdf", 2, "c2")),
        mode="mix",
        answer_points=(AnswerPoint("P1", "结论", ("UNKNOWN",), "supported"),),
    )
    citations = [
        _citation_response(item, index, evidence_id=f"E{index}")
        for index, item in enumerate(result.citations, start=1)
    ]
    claim = _claims_for_result(result, citations)[0]
    assert claim.citation_ids == []
    assert claim.evidence_ids == []
