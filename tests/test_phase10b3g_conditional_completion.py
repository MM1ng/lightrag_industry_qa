from industrial_rag.conditional_completion import plan_conditional_completion
from industrial_rag.evidence_completion import ContextRecord


def record(chunk_id, text, *, doc="d1", gen="g1", **links):
    return ContextRecord("kb", gen, doc, "manual.pdf", chunk_id, text, 1, **links)


def test_complete_selection_does_not_trigger_completion():
    selected = [record("c1", "泵的轴承温度参数为 80 °C，适用条件为正常运行")]
    result = plan_conditional_completion("parameter", selected, {}, max_completion=2)
    assert result.accepted == ()
    assert result.missing == ()


def test_parent_is_selected_only_for_a_real_coverage_gap():
    selected = [record("c1", "泵轴承温度参数为 80 °C", parent_chunk_id="p1")]
    registry = {"p1": record("p1", "适用条件：正常运行；设备对象：泵")}
    result = plan_conditional_completion("parameter", selected, registry)
    assert [item.chunk_id for item in result.accepted] == ["p1"]
    assert result.accepted[0].relation == "parent"
    assert "condition" in result.missing


def test_adjacent_requires_remaining_gap_after_parent_and_is_bounded():
    selected = [record("c2", "执行步骤：拆下滤网", parent_chunk_id="p2", previous_chunk_id="c1", next_chunk_id="c3")]
    registry = {
        "p2": record("p2", "前置条件：停机并断电"),
        "c1": record("c1", "警告：禁止带电操作"),
        "c3": record("c3", "步骤：重新安装滤网"),
    }
    result = plan_conditional_completion("procedure", selected, registry, max_completion=2)
    assert [item.chunk_id for item in result.accepted] == ["p2", "c1"]
    assert result.reasons["adjacent"] == "remaining_gap"


def test_cross_document_and_generation_candidates_are_rejected():
    selected = [record("c2", "参数名：压力", parent_chunk_id="p1", next_chunk_id="c3")]
    registry = {
        "p1": record("p1", "单位 MPa", doc="other"),
        "c3": record("c3", "数值 2", gen="old"),
    }
    result = plan_conditional_completion("parameter", selected, registry)
    assert result.accepted == ()
    assert {item.chunk_id for item in result.rejected} == {"p1", "c3"}


def test_negative_question_never_uses_adjacent_completion():
    selected = [record("c2", "现象：压力异常", previous_chunk_id="c1")]
    registry = {"c1": record("c1", "原因：过滤器堵塞")}
    result = plan_conditional_completion("troubleshooting", selected, registry, is_negative=True)
    assert result.accepted == ()
    assert result.reasons["negative"] == "adjacent_disabled"


def test_unrelated_adjacent_is_rejected_and_order_is_stable():
    selected = [record("c2", "参数名：压力", previous_chunk_id="c1", next_chunk_id="c3")]
    registry = {
        "c1": record("c1", "目录和版权信息"),
        "c3": record("c3", "单位 MPa，数值 2"),
    }
    result = plan_conditional_completion("parameter", selected, registry)
    assert [item.chunk_id for item in result.accepted] == ["c3"]
    assert result.reasons["candidate_order"] == ["c1", "c3"]


def test_maximum_two_and_no_duplicate_candidates():
    selected = [record("c2", "步骤：拆卸", parent_chunk_id="p", previous_chunk_id="a", next_chunk_id="b")]
    registry = {
        "p": record("p", "前置条件：停机"),
        "a": record("a", "警告：佩戴防护用品"),
        "b": record("b", "步骤：安装"),
    }
    result = plan_conditional_completion("procedure", selected, registry, max_completion=2)
    assert len(result.accepted) <= 2
    assert len({item.chunk_id for item in result.accepted}) == len(result.accepted)
