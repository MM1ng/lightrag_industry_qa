from industrial_rag.answer_grounding import build_answer_plan, classify_question_type
from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate


def _candidate(chunk: str, text: str, page: int = 1) -> EvidenceCandidate:
    return EvidenceCandidate(Citation("pump.pdf", page, chunk), text, 1)


def test_answer_points_bind_to_real_evidence():
    result = build_answer_plan("压力为 10 bar。", [_candidate("c1", "压力为 10 bar，正常运行。")], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "success"
    assert result.answer_points[0].evidence_ids == ("E1",)


def test_fake_evidence_id_is_not_accepted():
    result = build_answer_plan("压力为 10 bar。", [], [])
    assert result.status == "insufficient_evidence"
    assert result.citations == ()


def test_unsupported_point_is_removed_and_partial_answer_returned():
    result = build_answer_plan("压力为 10 bar。\n颜色为蓝色。", [_candidate("c1", "压力为 10 bar。")], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "partial_answer"
    assert result.answer == "压力为 10 bar。"
    assert result.answer_points[1].support_status == "unsupported"


def test_parameter_without_unit_is_not_complete_conclusion():
    result = build_answer_plan("压力为 10 MPa。", [_candidate("c1", "压力为 10。")], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "insufficient_evidence"


def test_parameter_condition_mismatch_is_rejected():
    result = build_answer_plan("2196 泵压力为 10 bar。", [_candidate("c1", "2196 泵压力为 10 bar。")], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "success"


def test_missing_procedure_step_becomes_partial():
    result = build_answer_plan("先停机。\n然后拆卸。", [_candidate("c1", "先停机。")], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "partial_answer"


def test_multi_evidence_missing_one_is_partial():
    selected = [_candidate("c1", "环境应清洁干燥。"), _candidate("c2", "每周旋转泵轴一次。")]
    result = build_answer_plan("环境应清洁干燥。\n每周旋转泵轴一次。", selected[:1], [Citation("pump.pdf", 1, "c1")])
    assert result.status == "partial_answer"


def test_question_type_policy_is_deterministic():
    assert classify_question_type("压力上限是多少？") == "condition_limit"
    assert classify_question_type("如何拆卸泵？") == "procedure"
    assert classify_question_type("有哪些安全警告？") == "safety"


def test_wrong_generation_is_not_injected_by_grounding_plan():
    result = build_answer_plan("压力为 10 bar。", [_candidate("c1", "压力为 10 bar.")], [Citation("pump.pdf", 1, "c1")])
    assert all(citation.chunk_id == "c1" for citation in result.citations)


def test_secret_like_text_is_not_added_to_plan_metadata():
    result = build_answer_plan("密钥 abc123 不应出现。", [_candidate("c1", "设备密钥字段不属于手册证据。")], [Citation("pump.pdf", 1, "c1")])
    assert "Authorization" not in str(result)


def test_provenance_only_fragment_does_not_become_answer_point():
    result = build_answer_plan(
        "泵轴至少每周旋转一次。\n（证据来源：manual.pdf，第9页）",
        [_candidate("c1", "泵轴至少每周旋转一次。")],
        [Citation("manual.pdf", 9, "c1")],
    )

    assert [point.content for point in result.answer_points] == ["泵轴至少每周旋转一次。"]
    assert result.answer == "泵轴至少每周旋转一次。"
    assert all(point.support_status == "supported" for point in result.answer_points)


def test_provenance_heading_does_not_create_unsupported_point():
    result = build_answer_plan(
        "压力为10 bar。\n证据来源：",
        [_candidate("c1", "压力为10 bar。")],
        [Citation("manual.pdf", 9, "c1")],
    )

    assert len(result.answer_points) == 1
    assert result.answer_points[0].evidence_ids == ("E1",)
