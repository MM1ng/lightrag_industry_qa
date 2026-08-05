from __future__ import annotations

from industrial_rag.evidence_answer_schema import EvidenceRef
from industrial_rag.structured_citation_output import (
    RequirementRegistry,
    SourceRegistry,
    validate_structured_citation_output,
)


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
