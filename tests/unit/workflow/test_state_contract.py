from __future__ import annotations

from pathlib import Path

from industrial_energy_agent.agents.state import (
    merge_documents,
    merge_errors,
    merge_fault_cases,
    merge_sensor_evidence,
    merge_traces,
)
from industrial_energy_agent.domain.errors import StructuredError
from industrial_energy_agent.domain.models import (
    ManualCitation,
    SensorCitation,
    SyntheticCitation,
    TraceEvent,
)
from industrial_energy_agent.workflow.nodes import (
    build_evaluate_evidence,
    build_retrieve_evidence,
    finalize_status,
    initialize,
    intent_router,
    rewrite_retrieval_query,
    safety_precheck,
    safety_review,
)


def _manual(citation_id: str) -> ManualCitation:
    return ManualCitation(
        citation_id=citation_id,
        document_title="手册",
        page_number=1,
        chunk_id=citation_id,
        excerpt="证据",
    )


def _sensor(citation_id: str, cycle_id: int) -> SensorCitation:
    return SensorCitation(
        citation_id=citation_id,
        dataset="hydraulic",
        cycle_id=cycle_id,
        artifact_version="sha256:" + "a" * 64,
        features={"PS1__mean": 1.0},
        units={"PS1__mean": "bar"},
    )


def _case(citation_id: str) -> SyntheticCitation:
    return SyntheticCitation(citation_id=citation_id, entity_id="PUMP-001", case_id=citation_id)


def _trace(node: str, duration_ms: float) -> TraceEvent:
    return TraceEvent(
        request_id="req-1",
        node=node,
        action="search",
        status="success",
        duration_ms=duration_ms,
        tool="manual",
    )


def test_collection_reducers_deduplicate_identities_without_losing_distinct_values() -> None:
    one = _manual("manual-1")
    two = _manual("manual-2")
    assert merge_documents([one], [one, two]) == [one, two]

    sensor_one = _sensor("sensor-1", 1)
    sensor_two = _sensor("sensor-2", 2)
    assert merge_sensor_evidence([sensor_one], [sensor_one, sensor_two]) == [sensor_one, sensor_two]

    case_one = _case("case-1")
    case_two = _case("case-2")
    assert merge_fault_cases([case_one], [case_one, case_two]) == [case_one, case_two]

    trace_one = _trace("retrieve_manual", 1.0)
    trace_two = _trace("retrieve_sensor", 2.0)
    assert merge_traces([trace_one], [trace_one, trace_two]) == [trace_one, trace_two]

    error_one = StructuredError(
        code="RAG_ERROR", message="失败", retryable=True, request_id="req-1"
    )
    error_two = StructuredError(
        code="SENSOR_ERROR", message="失败", retryable=False, request_id="req-1"
    )
    assert merge_errors([error_one], [error_one, error_two]) == [error_one, error_two]


def test_only_finalizer_writes_public_workflow_status() -> None:
    state = {
        "user_query": "轴承问题",
        "conversation_id": "c1",
        "request_id": "req-1",
        "retrieval_query": "轴承问题",
        "retry_count": 0,
        "http_retry_count": 0,
        "documents": [],
        "sensor_evidence": [],
        "fault_cases": [],
        "traces": [],
        "errors": [],
        "safety_assessment": None,
        "evidence_decision": "INSUFFICIENT_EVIDENCE",
        "retrieval_new_evidence_count": 0,
    }

    updates = [
        initialize(state),
        safety_precheck(state),
        intent_router(state),
        build_retrieve_evidence(
            manual_search=lambda query: [],
            sensor_search=lambda query: [],
            case_search=lambda query: [],
            max_http_retries=2,
        )(state),
        build_evaluate_evidence(lambda documents, sensors, cases: False)(state),
        rewrite_retrieval_query(state),
        safety_review(state),
    ]

    assert all("workflow_status" not in update for update in updates)
    assert finalize_status(state)["workflow_status"] == "INSUFFICIENT_EVIDENCE"


def test_graph_node_registration_does_not_escape_type_checking_with_any_cast() -> None:
    graph_source = (
        Path(__file__).resolve().parents[3] / "src/industrial_energy_agent/workflow/graph.py"
    ).read_text(encoding="utf-8")

    assert "cast(Any" not in graph_source
