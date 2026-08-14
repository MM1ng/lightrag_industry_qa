from __future__ import annotations

from industrial_rag.evidence_answer_schema import EvidenceRef
from industrial_rag.structured_citation_output import (
    RequirementRegistry,
    SourceRegistry,
    StructuredCitationPoint,
    render_public_citation_numbers,
    validate_structured_citation_output,
)
from pydantic import ValidationError


def _evidence(chunk_id: str, *, generation_id: str = "g1") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"E-{chunk_id}",
        chunk_id=chunk_id,
        generation_id=generation_id,
        document_name="manual.pdf",
        citation_id=f"cite-{chunk_id}",
        text=f"child text for {chunk_id}",
        is_child=True,
    )


def _registry() -> SourceRegistry:
    return SourceRegistry.from_evidence((_evidence("c2"), _evidence("c1")))


def _requirements(*values: str) -> RequirementRegistry:
    return RequirementRegistry.from_requirements(values)


def test_source_ids_follow_child_provider_order() -> None:
    registry = _registry()

    assert registry.source_ids == ("S1", "S2")
    assert registry.resolve("S1").chunk_id == "c2"
    assert registry.resolve("S2").evidence_id == "E-c1"


def test_status_is_success_for_points_without_unresolved_requirements() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[{"text":"答案","source_ids":["S1"]}],'
        '"unresolved_requirement_ids":[]}',
        _registry(),
        _requirements(),
        "g1",
    )

    assert decision.status == "success"
    assert decision.valid is True


def test_status_is_partial_for_points_with_unresolved_requirements() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[{"text":"答案","source_ids":["S1"]}],'
        '"unresolved_requirement_ids":["R1"]}',
        _registry(),
        _requirements("need another fact"),
        "g1",
    )

    assert decision.status == "partial_answer"
    assert decision.valid is True


def test_status_is_insufficient_for_empty_points() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[],"unresolved_requirement_ids":["R1"]}',
        _registry(),
        _requirements("need a fact"),
        "g1",
    )

    assert decision.status == "insufficient_evidence"
    assert decision.valid is True


def _decision_or_none(
    payload: str,
    registry: SourceRegistry | None = None,
    requirements: RequirementRegistry | None = None,
    generation_id: str = "g1",
):
    try:
        return validate_structured_citation_output(
            payload,
            registry or _registry(),
            requirements or _requirements(),
            generation_id,
        )
    except ValidationError:
        return None


def test_unknown_source_uses_atomic_j0_postprocessing() -> None:
    decision = _decision_or_none(
        '{"answer_points":[{"text":"答案","source_ids":["S9"]}],'
        '"unresolved_requirement_ids":[]}'
    )

    assert decision is not None
    assert decision.fallback_mode == "fallback_to_j0_postprocessing"


def test_wrong_generation_uses_atomic_j0_postprocessing() -> None:
    decision = _decision_or_none(
        '{"answer_points":[{"text":"答案","source_ids":["S1"]}],'
        '"unresolved_requirement_ids":[]}',
        SourceRegistry.from_evidence((_evidence("c1", generation_id="other"),)),
    )

    assert decision is not None
    assert decision.fallback_mode == "fallback_to_j0_postprocessing"


def test_more_than_two_or_duplicate_sources_uses_atomic_j0_postprocessing() -> None:
    decision = _decision_or_none(
        '{"answer_points":[{"text":"答案","source_ids":["S1","S1","S2"]}],'
        '"unresolved_requirement_ids":[]}'
    )

    assert decision is not None
    assert decision.fallback_mode == "fallback_to_j0_postprocessing"


def test_missing_answer_points_uses_safe_failure() -> None:
    decision = _decision_or_none('{"status":"success"}')

    assert decision is not None
    assert decision.fallback_mode == "safe_failure_no_second_generation"


def test_unknown_requirement_uses_atomic_j0_postprocessing() -> None:
    decision = _decision_or_none(
        '{"answer_points":[{"text":"答案","source_ids":["S1"]}],'
        '"unresolved_requirement_ids":["R9"]}',
        requirements=_requirements("known requirement"),
    )

    assert decision is not None
    assert decision.fallback_mode == "fallback_to_j0_postprocessing"


def test_parent_without_real_child_never_enters_source_registry() -> None:
    parent = EvidenceRef(
        evidence_id="E-parent",
        chunk_id="parent-1",
        generation_id="g1",
        document_name="manual.pdf",
        citation_id=None,
        text="parent text must not masquerade as a child",
        is_child=False,
    )

    assert SourceRegistry.from_evidence((parent,)).source_ids == ()


def test_public_citation_numbers_follow_first_answer_use_not_source_order() -> None:
    numbers = render_public_citation_numbers(
        (
            StructuredCitationPoint("first", ("S2",)),
            StructuredCitationPoint("second", ("S1", "S2")),
        )
    )

    assert numbers == ((1,), (2, 1))


def test_repeated_source_reuses_its_public_citation_number() -> None:
    numbers = render_public_citation_numbers(
        (
            StructuredCitationPoint("first", ("S1",)),
            StructuredCitationPoint("second", ("S1",)),
        )
    )

    assert numbers == ((1,), (1,))


def test_provenance_only_structured_point_is_not_semantic_answer_point() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[{"text":"[[INDUSTRIAL_RAG_SOURCE file=manual.pdf page=9 chunk=c1]]",'
        '"source_ids":["S1"]}],"unresolved_requirement_ids":[]}',
        _registry(),
        _requirements(),
        "g1",
    )

    assert decision.answer_points == ()
    assert decision.status == "insufficient_evidence"


def test_structured_factual_point_keeps_fact_and_strips_internal_marker() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[{"text":"泵轴每周旋转一次。[[INDUSTRIAL_RAG_SOURCE file=manual.pdf page=9 chunk=c1]]",'
        '"source_ids":["S1"]}],"unresolved_requirement_ids":[]}',
        _registry(),
        _requirements(),
        "g1",
    )

    assert decision.answer_points[0].text == "泵轴每周旋转一次。"
