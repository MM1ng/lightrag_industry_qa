from industrial_rag.post_retrieval_recovery import evaluate_post_retrieval_recovery


def _item(chunk_id: str, text: str, **extra):
    return {"chunk_id": chunk_id, "document_id": "doc-1", "generation_id": "gen-1", "text": text, **extra}


def test_recalled_not_selected_is_bounded_and_deterministic():
    selected = [_item("c1", "泵压力 参数 为 2 MPa", next_chunk_id="c2")]
    candidates = [_item("c2", "适用条件 正常运行"), _item("c3", "无关内容")]
    result = evaluate_post_retrieval_recovery(
        question_type="parameter",
        selected=selected,
        available_candidates=candidates,
        coverage_requirements=("condition",),
        max_recovery_candidates=1,
    )
    assert result.kind == "recalled_not_selected"
    assert result.accepted_chunk_ids == ("c2",)
    assert result.candidate_chunk_ids == ("c2",)


def test_grounding_false_negative_never_changes_global_threshold():
    result = evaluate_post_retrieval_recovery(
        question_type="parameter",
        selected=[_item("c1", "压力 参数 值 2 MPa")],
        provider_evidence_ids=("c1",),
        generated_answer_point_ids=("P1",),
        grounding_removed_point_ids=("P1",),
    )
    assert result.kind == "grounding_false_negative"
    assert result.action == "grounding_review_replay"
    assert "threshold" not in result.reason


def test_generation_refusal_requires_provider_context():
    without_context = evaluate_post_retrieval_recovery(
        question_type="safety",
        selected=[_item("c1", "禁止带电操作")],
        generation_status="insufficient_evidence",
    )
    assert without_context.kind == "generation_refusal"
    assert without_context.eligible is False

    with_context = evaluate_post_retrieval_recovery(
        question_type="safety",
        selected=[_item("c1", "禁止带电操作")],
        provider_evidence_ids=("c1",),
        generation_status="insufficient_evidence",
    )
    assert with_context.kind == "generation_refusal"
    assert with_context.eligible is True


def test_negative_query_does_not_enable_adjacent_recovery():
    result = evaluate_post_retrieval_recovery(
        question_type="parameter",
        selected=[_item("c1", "压力 参数 值 2 MPa", next_chunk_id="c2")],
        available_candidates=[_item("c2", "适用条件 正常运行")],
        coverage_requirements=("condition",),
        negative_query=True,
    )
    assert result.kind == "none"


def test_max_recovery_candidates_is_enforced():
    result = evaluate_post_retrieval_recovery(
        question_type="parameter",
        selected=[_item("c1", "压力 参数 值 2 MPa", next_chunk_id="c2", previous_chunk_id="c3")],
        available_candidates=[_item("c2", "适用条件 正常运行"), _item("c3", "适用条件 停机")],
        coverage_requirements=("condition",),
        max_recovery_candidates=1,
    )
    assert len(result.accepted_chunk_ids) <= 1
