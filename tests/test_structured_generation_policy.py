from industrial_rag.evidence_answer_schema import EvidenceRef, StructuredAnswerPoint
from industrial_rag.structured_generation_parser import parse_structured_answer
from industrial_rag.structured_generation_policy import validate_answer_points

GEN = "g1"


def ref(eid: str, text: str, *, citation: str | None = "cite_1", generation: str = GEN, child: bool = True):
    return EvidenceRef(eid, f"chunk-{eid}", generation, citation_id=citation, text=text, is_child=child)


def test_parser_accepts_structured_fields_and_never_invents_ids():
    parsed = parse_structured_answer(
        '{"answer":"压力为5 MPa","answer_points":[{"point_id":"P1","text":"压力为5 MPa","evidence_ids":["E1"],"parameter":"压力","numeric_values":["5"],"units":["MPa"]}]}'
    )
    assert parsed.points[0].evidence_ids == ("E1",)
    assert parsed.points[0].units == ("MPa",)


def test_parser_failure_returns_safe_fallback_without_second_call():
    parsed = parse_structured_answer("not-json", fallback_answer="原始回答")
    assert parsed.answer == "原始回答"
    assert parsed.parse_error == "invalid_json"


def test_unknown_evidence_is_removed_not_repaired():
    point = StructuredAnswerPoint("P1", "压力为5 MPa", ("UNKNOWN",))
    result = validate_answer_points((point,), [ref("E1", "压力为5 MPa")], generation_id=GEN)
    assert result.status == "insufficient_evidence"
    assert result.points == ()
    assert result.point_results[0].reason == "unknown_evidence_id"


def test_wrong_generation_is_rejected():
    point = StructuredAnswerPoint("P1", "压力为5 MPa", ("E1",))
    result = validate_answer_points((point,), [ref("E1", "压力为5 MPa", generation="other")], generation_id=GEN)
    assert result.invalid_point_ids == ("P1",)
    assert "wrong_generation" in (result.point_results[0].reason or "")


def test_parent_context_must_have_real_child_citation():
    point = StructuredAnswerPoint("P1", "压力为5 MPa", ("E1",))
    parent = ref("E1", "压力为5 MPa", citation=None, child=False)
    result = validate_answer_points((point,), [parent], generation_id=GEN)
    assert result.status == "insufficient_evidence"
    parent_with_child = ref("E1", "压力为5 MPa", citation="cite_child", child=False)
    assert validate_answer_points((point,), [parent_with_child], generation_id=GEN).status == "success"


def test_negation_mismatch_does_not_pass_from_token_overlap():
    point = StructuredAnswerPoint("P1", "不得超过5 MPa", ("E1",))
    permissive = ref("E1", "允许超过5 MPa")
    assert validate_answer_points((point,), [permissive], generation_id=GEN).status == "insufficient_evidence"
    prohibited = ref("E1", "不得超过5 MPa")
    assert validate_answer_points((point,), [prohibited], generation_id=GEN).status == "success"


def test_numeric_and_unit_requirements_are_checked():
    point = StructuredAnswerPoint("P1", "压力为5 MPa", ("E1",), numeric_values=("5",), units=("MPa",))
    missing = ref("E1", "压力为6 bar")
    assert validate_answer_points((point,), [missing], generation_id=GEN).status == "insufficient_evidence"


def test_one_invalid_point_does_not_reject_valid_point():
    points = (
        StructuredAnswerPoint("P1", "压力为5 MPa", ("E1",)),
        StructuredAnswerPoint("P2", "不存在的结论", ("UNKNOWN",)),
    )
    result = validate_answer_points(points, [ref("E1", "压力为5 MPa")], generation_id=GEN)
    assert result.status == "partial_answer"
    assert [p.point_id for p in result.points] == ["P1"]
    assert result.invalid_point_ids == ("P2",)


def test_all_invalid_safety_points_are_safety_blocked():
    point = StructuredAnswerPoint("P1", "禁止在高温下操作", ("E1",))
    result = validate_answer_points((point,), [], generation_id=GEN, safety_question=True)
    assert result.status == "safety_blocked"
