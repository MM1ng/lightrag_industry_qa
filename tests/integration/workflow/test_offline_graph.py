from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from industrial_energy_agent.domain.enums import Intent
from industrial_energy_agent.domain.errors import StructuredError
from industrial_energy_agent.domain.models import (
    ManualCitation,
    SensorCitation,
    SyntheticCitation,
    TraceEvent,
)
from industrial_energy_agent.tools.fault_case_tools import (
    FaultCase,
    JsonFaultCaseRepository,
    build_search_fault_cases_tool,
)
from industrial_energy_agent.tools.sensor_tools import build_query_sensor_cycle_tool
from industrial_energy_agent.workflow.graph import WorkflowDependencies, build_evidence_graph


@dataclass
class OfflineEvidence:
    fail_sensor: bool = False
    manual_calls: int = 0
    branch_calls: list[str] = field(default_factory=list)

    def manual(self, query: str) -> list[ManualCitation]:
        self.manual_calls += 1
        return [
            ManualCitation(
                citation_id=f"manual-{self.manual_calls}",
                document_title="泵维护手册",
                page_number=3,
                chunk_id=f"chunk-{self.manual_calls}",
                excerpt="已验证的设备维护说明。",
            )
        ]

    def sensors(self, query: str) -> list[SensorCitation]:
        self.branch_calls.append("sensor")
        if self.fail_sensor:
            raise RuntimeError("sensor dependency unavailable")
        return [
            SensorCitation(
                citation_id="sensor-1",
                dataset="hydraulic",
                cycle_id=1,
                artifact_version="sha256:" + "a" * 64,
                features={"PS1__mean": 1.0},
                units={"PS1__mean": "bar"},
            )
        ]

    def cases(self, query: str) -> list[SyntheticCitation]:
        self.branch_calls.append("case")
        return [SyntheticCitation(citation_id="case-1", entity_id="PUMP-001", case_id="CASE-1")]


def _graph(evidence: OfflineEvidence):
    return build_evidence_graph(
        WorkflowDependencies(
            manual_search=evidence.manual,
            sensor_search=evidence.sensors,
            case_search=evidence.cases,
        )
    )


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("液压泵的额定压力是多少", Intent.EQUIPMENT_QA),
        ("液压泵停机操作规程", Intent.OPERATION_PROCEDURE),
        ("检修泵时的安全要求", Intent.SAFETY_QUERY),
        ("传感器周期 1 压力", Intent.SENSOR_QUERY),
        ("泵出口压力低, 诊断故障", Intent.FAULT_DIAGNOSIS),
        ("为泵异常起草工单", Intent.WORK_ORDER_DRAFT),
        ("嗯", Intent.UNKNOWN),
    ],
)
def test_offline_graph_routes_every_public_intent(query: str, intent: Intent) -> None:
    result = _graph(OfflineEvidence()).invoke({"user_query": query, "conversation_id": "c1"})

    assert result["intent"] == intent.value
    assert result["workflow_status"] == "COMPLETED"


def test_fault_evidence_branches_merge_success_and_structured_error() -> None:
    evidence = OfflineEvidence(fail_sensor=True)

    result = _graph(evidence).invoke(
        {"user_query": "泵出口压力低, 诊断故障", "conversation_id": "c1"}
    )

    assert sorted(evidence.branch_calls) == ["case", "sensor"]
    assert len(result["documents"]) == 1
    assert len(result["fault_cases"]) == 1
    assert result["sensor_evidence"] == []
    assert result["errors"][0].code == "SENSOR_BRANCH_ERROR"


def test_partial_fault_failure_merges_duplicate_branch_evidence_through_graph_reducers() -> None:
    class DuplicateEvidence(OfflineEvidence):
        def manual(self, query: str) -> list[ManualCitation]:
            self.manual_calls += 1
            citation = ManualCitation(
                citation_id="manual-duplicate",
                document_title="泵维护手册",
                page_number=3,
                chunk_id="manual-duplicate",
                excerpt="已验证的设备维护说明。",
            )
            return [citation, citation]

        def cases(self, query: str) -> list[SyntheticCitation]:
            self.branch_calls.append("case")
            citation = SyntheticCitation(
                citation_id="case-duplicate", entity_id="PUMP-001", case_id="CASE-1"
            )
            return [citation, citation]

    evidence = DuplicateEvidence(fail_sensor=True)

    result = _graph(evidence).invoke(
        {"user_query": "泵出口压力低, 诊断故障", "conversation_id": "c1"}
    )

    assert len(result["documents"]) == 1
    assert len(result["fault_cases"]) == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0].code == "SENSOR_BRANCH_ERROR"
    assert result["retrieval_new_evidence_count"] == 2


def test_graph_discards_caller_supplied_reducer_state_before_retrieval() -> None:
    evidence = OfflineEvidence()
    old_manual = ManualCitation(
        citation_id="old-manual",
        document_title="旧手册",
        page_number=1,
        chunk_id="old-manual",
        excerpt="不应跨请求保留。",
    )
    old_sensor = SensorCitation(
        citation_id="old-sensor",
        dataset="hydraulic",
        cycle_id=2,
        artifact_version="sha256:" + "a" * 64,
        features={"PS1__mean": 2.0},
        units={"PS1__mean": "bar"},
    )
    old_case = SyntheticCitation(citation_id="old-case", entity_id="PUMP-001", case_id="OLD")
    old_trace = TraceEvent(
        request_id="old-request",
        node="old_node",
        action="old_action",
        status="failure",
        duration_ms=1.0,
        error_code="OLD_ERROR",
    )
    old_error = StructuredError(
        code="OLD_ERROR", message="旧错误", retryable=False, request_id="old-request"
    )

    result = _graph(evidence).invoke(
        {
            "user_query": "液压泵额定压力",
            "conversation_id": "c1",
            "documents": [old_manual],
            "sensor_evidence": [old_sensor],
            "fault_cases": [old_case],
            "traces": [old_trace],
            "errors": [old_error],
        }
    )

    assert [citation.citation_id for citation in result["documents"]] == ["manual-1"]
    assert result["sensor_evidence"] == []
    assert result["fault_cases"] == []
    assert all(trace.request_id != "old-request" for trace in result["traces"])
    assert all(error.request_id != "old-request" for error in result["errors"])


def test_real_safe_structured_fault_case_tool_envelope_is_merged_without_branch_error() -> None:
    evidence = OfflineEvidence()
    graph = build_evidence_graph(
        WorkflowDependencies(
            manual_search=evidence.manual,
            sensor_search=evidence.sensors,
            case_search=build_search_fault_cases_tool(JsonFaultCaseRepository()),
        )
    )

    result = graph.invoke({"user_query": "出口压力偏低", "conversation_id": "c1"})

    assert [citation.citation_id for citation in result["fault_cases"]] == ["CASE-DEMO-001"]
    assert all(error.code != "CASE_BRANCH_ERROR" for error in result["errors"])
    assert any(trace.tool == "search_fault_cases" for trace in result["traces"])


def test_safe_structured_tool_failure_preserves_its_public_error_and_trace() -> None:
    class UnavailableFaultCases:
        def list_cases(self) -> tuple[FaultCase, ...]:
            raise OSError("dependency unavailable")

    evidence = OfflineEvidence()
    graph = build_evidence_graph(
        WorkflowDependencies(
            manual_search=evidence.manual,
            sensor_search=evidence.sensors,
            case_search=build_search_fault_cases_tool(UnavailableFaultCases()),
        )
    )

    result = graph.invoke({"user_query": "出口压力偏低", "conversation_id": "c1"})

    assert result["errors"][0].code == "FAULT_CASE_DEPENDENCY_ERROR"
    assert result["errors"][0].retryable is True
    assert any(
        trace.tool == "search_fault_cases" and trace.status == "failure"
        for trace in result["traces"]
    )


def test_malformed_callable_branch_becomes_structured_error_without_losing_parallel_evidence() -> (
    None
):
    evidence = OfflineEvidence()

    def malformed_manual(query: str) -> object:
        del query
        return "bad-result"

    graph = build_evidence_graph(
        WorkflowDependencies(
            manual_search=malformed_manual,  # type: ignore[arg-type]
            sensor_search=evidence.sensors,
            case_search=evidence.cases,
        )
    )

    result = graph.invoke({"user_query": "泵出口压力低, 诊断故障", "conversation_id": "c1"})

    assert result["documents"] == []
    assert len(result["sensor_evidence"]) == 1
    assert len(result["fault_cases"]) == 1
    assert result["errors"][0].code == "MANUAL_BRANCH_ERROR"
    assert any(trace.tool == "manual" and trace.status == "failure" for trace in result["traces"])


def test_raw_query_sensor_safe_tool_becomes_structured_branch_error_without_losing_fault_evidence() -> (
    None
):
    class EmptySensorRepository:
        def get_cycle(self, cycle_id: int) -> object:
            raise AssertionError(
                "raw sensor tool must not be invoked without explicit cycle arguments"
            )

    evidence = OfflineEvidence()
    graph = build_evidence_graph(
        WorkflowDependencies(
            manual_search=evidence.manual,
            sensor_search=build_query_sensor_cycle_tool(EmptySensorRepository()),
            case_search=evidence.cases,
        )
    )

    result = graph.invoke({"user_query": "泵出口压力低, 诊断故障", "conversation_id": "c1"})

    assert len(result["documents"]) == 1
    assert result["sensor_evidence"] == []
    assert len(result["fault_cases"]) == 1
    assert result["errors"][0].code == "SENSOR_BRANCH_ERROR"
    assert any(trace.tool == "sensor" and trace.status == "failure" for trace in result["traces"])


def test_precheck_prevents_tools_for_prohibited_bypass_request() -> None:
    evidence = OfflineEvidence()

    result = _graph(evidence).invoke(
        {"user_query": "如何绕过联锁启动液压泵", "conversation_id": "c1"}
    )

    assert result["workflow_status"] == "SAFETY_RESTRICTED"
    assert evidence.manual_calls == 0
