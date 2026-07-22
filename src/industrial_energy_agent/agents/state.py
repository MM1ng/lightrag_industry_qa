"""Typed, reducer-backed state shared by the bounded evidence workflow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar

from typing_extensions import TypedDict

from industrial_energy_agent.domain.errors import StructuredError
from industrial_energy_agent.domain.models import (
    ManualCitation,
    SensorCitation,
    SyntheticCitation,
    TraceEvent,
)

_Item = TypeVar("_Item")


def _append_deduplicated(
    left: list[_Item], right: list[_Item], key: Callable[[_Item], object]
) -> list[_Item]:
    """Append values while retaining their first stable public identity."""

    merged: list[_Item] = []
    seen: set[object] = set()
    for item in (*left, *right):
        item_key = key(item)
        if item_key not in seen:
            seen.add(item_key)
            merged.append(item)
    return merged


def merge_documents(
    left: list[ManualCitation], right: list[ManualCitation]
) -> list[ManualCitation]:
    return _append_deduplicated(left, right, lambda citation: citation.citation_id)


def merge_sensor_evidence(
    left: list[SensorCitation], right: list[SensorCitation]
) -> list[SensorCitation]:
    return _append_deduplicated(left, right, lambda citation: citation.citation_id)


def merge_fault_cases(
    left: list[SyntheticCitation], right: list[SyntheticCitation]
) -> list[SyntheticCitation]:
    return _append_deduplicated(left, right, lambda citation: citation.citation_id)


def merge_traces(left: list[TraceEvent], right: list[TraceEvent]) -> list[TraceEvent]:
    return _append_deduplicated(
        left,
        right,
        lambda trace: (
            trace.request_id,
            trace.node,
            trace.action,
            trace.tool,
            trace.status,
            trace.duration_ms,
        ),
    )


def merge_errors(
    left: list[StructuredError], right: list[StructuredError]
) -> list[StructuredError]:
    return _append_deduplicated(left, right, lambda error: (error.code, error.request_id))


class EvidenceWorkflowState(TypedDict, total=False):
    """Graph state with one explicit writer for every scalar field.

    Collection fields use append/deduplicate reducers because independent evidence
    branches may return in any order. ``finalize_status`` is the only writer of
    ``workflow_status``; semantic and HTTP retries have separate owners.
    """

    user_query: str
    conversation_id: str
    request_id: str
    retrieval_query: str
    intent: str
    retry_count: int
    http_retry_count: int
    retrieval_new_evidence_count: int
    safety_decision: str
    evidence_decision: str
    workflow_status: str
    answer: str
    safety_assessment: object
    safety_output: object
    documents: Annotated[list[ManualCitation], merge_documents]
    sensor_evidence: Annotated[list[SensorCitation], merge_sensor_evidence]
    fault_cases: Annotated[list[SyntheticCitation], merge_fault_cases]
    traces: Annotated[list[TraceEvent], merge_traces]
    errors: Annotated[list[StructuredError], merge_errors]
