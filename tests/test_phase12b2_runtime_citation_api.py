from industrial_rag.answer_grounding import AnswerPoint
from industrial_rag.api import _citation_response, _claims_and_runtime_citations
from industrial_rag.citation_formatter import Citation
from industrial_rag.lightrag_service import QueryResult


def _public_citations(result: QueryResult):
    return [
        _citation_response(item, index, evidence_id=f"E{index}")
        for index, item in enumerate(result.citations, start=1)
    ]


def test_runtime_projection_removes_context_only_top_level_citations() -> None:
    result = QueryResult(
        answer="结论",
        citations=(Citation("manual.pdf", 1, "c1"), Citation("manual.pdf", 2, "c2"), Citation("manual.pdf", 3, "c3")),
        mode="mix",
        answer_points=(AnswerPoint("P1", "结论", ("E1",), "supported"),),
    )
    claims, citations = _claims_and_runtime_citations(result, _public_citations(result))
    assert claims[0].citation_ids == ["cite_1"]
    assert [item.citation_id for item in citations] == ["cite_1"]


def test_runtime_projection_keeps_union_for_multiple_claims() -> None:
    result = QueryResult(
        answer="两个结论",
        citations=(Citation("manual.pdf", 1, "c1"), Citation("manual.pdf", 2, "c2"), Citation("manual.pdf", 3, "c3")),
        mode="mix",
        answer_points=(
            AnswerPoint("P1", "结论一", ("E1",), "supported"),
            AnswerPoint("P2", "结论二", ("E2",), "supported"),
        ),
    )
    claims, citations = _claims_and_runtime_citations(result, _public_citations(result))
    assert [item.citation_id for item in citations] == ["cite_1", "cite_2"]
    assert [claim.citation_ids for claim in claims] == [["cite_1"], ["cite_2"]]

