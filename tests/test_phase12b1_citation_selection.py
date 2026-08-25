from industrial_rag.citation_selection import select_minimal_supporting_citations


def citation(cid: str, chunk: str, *, parent: str | None = None, role: str = "primary") -> dict[str, str]:
    value = {"citation_id": cid, "chunk_id": chunk, "context_role": role}
    if parent is not None:
        value["parent_chunk_id"] = parent
    return value


def test_single_supporting_evidence_keeps_only_that_citation() -> None:
    result = select_minimal_supporting_citations(
        claims=[{"claim_id": "P1", "candidate_citation_ids": ["cite_1", "cite_2"], "supporting_citation_ids": ["cite_1"]}],
        citations=[citation("cite_1", "c1"), citation("cite_2", "c2")],
    )
    assert result.retained_citation_ids == ("cite_1",)
    assert result.removed_citation_ids == ("cite_2",)


def test_two_evidence_claim_keeps_both_supporting_citations() -> None:
    result = select_minimal_supporting_citations(
        claims=[{"claim_id": "P1", "candidate_citation_ids": ["cite_1", "cite_2"], "supporting_citation_ids": ["cite_1", "cite_2"]}],
        citations=[citation("cite_1", "c1"), citation("cite_2", "c2")],
    )
    assert result.retained_citation_ids == ("cite_1", "cite_2")


def test_unrelated_context_adjacent_and_parent_duplicates_are_not_added() -> None:
    result = select_minimal_supporting_citations(
        claims=[{"claim_id": "P1", "candidate_citation_ids": ["cite_child"], "supporting_citation_ids": ["cite_child"]}],
        citations=[
            citation("cite_parent", "parent", parent="root"),
            citation("cite_child", "child", parent="parent"),
            citation("cite_adjacent", "adjacent", role="context_only"),
        ],
    )
    assert result.retained_citation_ids == ("cite_child",)


def test_supporting_citation_is_not_deleted_when_unrelated_claim_has_extra_context() -> None:
    result = select_minimal_supporting_citations(
        claims=[
            {"claim_id": "P1", "candidate_citation_ids": ["cite_1", "cite_2"], "supporting_citation_ids": ["cite_1"]},
            {"claim_id": "P2", "candidate_citation_ids": ["cite_2"], "supporting_citation_ids": ["cite_2"]},
        ],
        citations=[citation("cite_1", "c1"), citation("cite_2", "c2")],
    )
    assert result.retained_citation_ids == ("cite_1", "cite_2")
    assert result.supporting_removed == ()


def test_multi_claim_mapping_and_stable_order() -> None:
    result = select_minimal_supporting_citations(
        claims=[
            {"claim_id": "P1", "candidate_citation_ids": ["cite_2", "cite_1"], "supporting_citation_ids": ["cite_1"]},
            {"claim_id": "P2", "candidate_citation_ids": ["cite_2"], "supporting_citation_ids": ["cite_2"]},
        ],
        citations=[citation("cite_1", "c1"), citation("cite_2", "c2")],
    )
    assert result.retained_citation_ids == ("cite_1", "cite_2")
    assert result.claim_citation_ids == {"P1": ("cite_1",), "P2": ("cite_2",)}


def test_same_chunk_duplicate_is_emitted_once() -> None:
    result = select_minimal_supporting_citations(
        claims=[{"claim_id": "P1", "candidate_citation_ids": ["cite_1", "cite_duplicate"], "supporting_citation_ids": ["cite_1", "cite_duplicate"]}],
        citations=[citation("cite_1", "same_chunk"), citation("cite_duplicate", "same_chunk")],
    )
    assert result.retained_citation_ids == ("cite_1",)
    assert result.removed_citation_ids == ("cite_duplicate",)


def test_parent_child_same_fact_prefers_direct_child_once() -> None:
    result = select_minimal_supporting_citations(
        claims=[{"claim_id": "P1", "candidate_citation_ids": ["cite_parent", "cite_child"], "supporting_citation_ids": ["cite_parent", "cite_child"]}],
        citations=[
            citation("cite_parent", "parent"),
            citation("cite_child", "child", parent="parent"),
        ],
    )
    assert result.retained_citation_ids == ("cite_child",)
    assert result.removed_citation_ids == ("cite_parent",)
