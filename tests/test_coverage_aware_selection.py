from industrial_rag.coverage_aware_selection import select_coverage_aware_evidence


def _item(chunk_id: str, text: str):
    return {"chunk_id": chunk_id, "text": text}


def test_selection_is_bounded_to_existing_top20_and_preserves_original_order():
    candidates = [
        _item("c1", "泵压力参数值为2 MPa"),
        _item("c2", "正常运行条件"),
        *[_item(f"c{index}", "无关内容") for index in range(3, 21)],
        _item("c21", "警告 禁止操作"),
    ]
    decision = select_coverage_aware_evidence(
        candidates,
        current_selection=(candidates[0],),
        coverage_requirements=("object", "parameter", "unit", "condition", "warning"),
    )
    assert decision.selected_chunk_ids == ("c1", "c2")
    assert decision.excluded_outside_top20 == ("c21",)
    assert decision.max_evidence == 5
    assert decision.to_dict()["retrieval_performed"] is False
    assert decision.to_dict()["rerank_performed"] is False


def test_selection_never_selects_more_than_five_or_mutates_input_order():
    candidates = [_item(f"c{index}", "泵压力参数值 MPa 正常运行 警告") for index in range(1, 21)]
    decision = select_coverage_aware_evidence(candidates, coverage_requirements=("object",))
    assert len(decision.selected_chunk_ids) <= 5
    assert decision.considered_chunk_ids == tuple(f"c{index}" for index in range(1, 21))
