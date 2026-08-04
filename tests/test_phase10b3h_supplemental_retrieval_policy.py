from industrial_rag.supplemental_retrieval_policy import (
    MAX_FINAL_EVIDENCE,
    SUPPLEMENTAL_TOP_K,
    build_supplemental_query,
    derive_coverage_requirements,
    run_supplemental_retrieval,
)


def _row(chunk_id: str, *, kb: str = "kb-1", generation: str = "gen-1") -> dict[str, str]:
    return {"chunk_id": chunk_id, "knowledge_base_id": kb, "generation_id": generation}


def test_requirements_are_derived_from_question_not_fixture_answers():
    assert derive_coverage_requirements("启动前有哪些操作步骤和安全警告？", "procedure") == (
        "precondition",
        "step",
        "warning",
    )


def test_no_retrieval_when_parent_or_adjacent_resolves_gap():
    called = []
    result = run_supplemental_retrieval(
        "泵启动前步骤是什么？",
        knowledge_base_id="kb-1",
        generation_id="gen-1",
        coverage_before=("precondition",),
        coverage_after_context=("step", "warning"),
        question_type="procedure",
        retrieve=lambda query: called.append(query) or [_row("new")],
    )
    assert result.triggered is False
    assert result.reason == "parent_adjacent_resolved"
    assert called == []


def test_negative_and_complete_queries_never_trigger():
    called = []
    for kwargs in (
        {"is_negative": True},
        {"coverage_before": ("object", "parameter", "value", "unit", "condition")},
    ):
        result = run_supplemental_retrieval(
            "这个限制是否存在？",
            knowledge_base_id="kb-1",
            generation_id="gen-1",
            question_type="parameter",
            retrieve=lambda query: called.append(query) or [_row("new")],
            **kwargs,
        )
        assert result.triggered is False
    assert called == []


def test_one_bounded_attempt_deduplicates_and_filters_identity():
    calls = []

    def retrieve(query):
        calls.append(query)
        assert query.top_k == SUPPLEMENTAL_TOP_K
        return (
            _row("old"),
            _row("new-1"),
            _row("new-1"),
            _row("wrong-kb", kb="kb-2"),
            _row("new-2"),
            _row("new-3"),
            _row("new-4"),
        )

    result = run_supplemental_retrieval(
        "泵启动前有哪些步骤？",
        knowledge_base_id="kb-1",
        generation_id="gen-1",
        selected=(_row("old"),),
        coverage_before=("precondition",),
        question_type="procedure",
        retrieve=retrieve,
    )
    assert result.triggered is True
    assert len(calls) == 1
    assert [item["chunk_id"] for item in result.accepted] == ["new-1", "new-2", "new-3", "new-4"]
    assert result.duplicate_chunk_ids == ("new-1",)
    assert result.wrong_identity_chunk_ids == ("wrong-kb",)
    assert len(result.accepted) + 1 <= MAX_FINAL_EVIDENCE
    assert result.to_dict()["supplemental_query"]["top_k"] == 5


def test_query_builder_preserves_question_and_identity():
    query = build_supplemental_query(
        "原始问题",
        knowledge_base_id="kb-1",
        generation_id="gen-1",
        coverage_gap=("step", "step", "warning"),
    )
    assert query.question == "原始问题"
    assert query.coverage_gap == ("step", "warning")
    assert query.top_k == 5
    assert query.attempt == 1

