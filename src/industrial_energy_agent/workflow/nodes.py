"""Side-effect-bounded nodes used by the evidence graph."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol, TypeAlias, cast, runtime_checkable
from uuid import uuid4

from langgraph.types import Overwrite

from industrial_energy_agent.agents.state import EvidenceWorkflowState
from industrial_energy_agent.domain.enums import Intent
from industrial_energy_agent.domain.errors import StructuredError
from industrial_energy_agent.domain.models import (
    ManualCitation,
    SensorCitation,
    SyntheticCitation,
    TraceEvent,
)
from industrial_energy_agent.domain.safety_rules import (
    SafetyAssessment,
    classify_input,
    review_output,
)
from industrial_energy_agent.tools.common import SafeStructuredTool
from industrial_energy_agent.tools.fault_case_tools import SearchFaultCasesResult
from industrial_energy_agent.tools.knowledge_tools import SearchManualKnowledgeResult
from industrial_energy_agent.tools.sensor_tools import (
    CompareSensorCyclesResult,
    QuerySensorCycleResult,
)
from industrial_energy_agent.workflow.routing import route_intent

EvidenceList: TypeAlias = list[ManualCitation] | list[SensorCitation] | list[SyntheticCitation]
ToolArgumentBuilder: TypeAlias = Callable[[str, str], dict[str, object]]


@dataclass(frozen=True)
class BranchOutcome:
    """Decoded branch result, retaining a tool's public trace and error contract."""

    evidence: EvidenceList
    trace: TraceEvent | None = None
    error: StructuredError | None = None


@runtime_checkable
class BranchAdapter(Protocol):
    """Typed boundary between the graph and a callable or SafeStructuredTool."""

    def execute(self, *, query: str, request_id: str) -> BranchOutcome: ...


@dataclass(frozen=True)
class CallableBranchAdapter:
    """Compatibility adapter for existing deterministic offline fakes."""

    callback: Callable[[str], EvidenceList]

    def execute(self, *, query: str, request_id: str) -> BranchOutcome:
        del request_id
        return BranchOutcome(evidence=self.callback(query))


@dataclass(frozen=True)
class SafeToolBranchAdapter:
    """Decode supported SafeStructuredTool envelopes without losing typed failures."""

    tool: SafeStructuredTool
    result_kind: Literal["manual", "sensor_cycle", "sensor_comparison", "case"]
    argument_builder: ToolArgumentBuilder

    def execute(self, *, query: str, request_id: str) -> BranchOutcome:
        payload: object = self.tool.invoke(self.argument_builder(query, request_id))
        if self.result_kind == "manual":
            manual_result = SearchManualKnowledgeResult.model_validate(payload).root
            if not manual_result.ok:
                return BranchOutcome(
                    evidence=[],
                    trace=manual_result.trace,
                    error=StructuredError(
                        code=manual_result.error.code,
                        message=manual_result.error.message,
                        retryable=manual_result.error.retryable,
                        request_id=request_id,
                    ),
                )
            return BranchOutcome(
                evidence=[item.citation for item in manual_result.items], trace=manual_result.trace
            )
        if self.result_kind == "sensor_cycle":
            sensor_cycle_result = QuerySensorCycleResult.model_validate(payload).root
            if not sensor_cycle_result.ok:
                return BranchOutcome(
                    evidence=[],
                    trace=sensor_cycle_result.trace,
                    error=StructuredError(
                        code=sensor_cycle_result.error.code,
                        message=sensor_cycle_result.error.message,
                        retryable=sensor_cycle_result.error.retryable,
                        request_id=request_id,
                    ),
                )
            return BranchOutcome(
                evidence=[sensor_cycle_result.cycle.citation], trace=sensor_cycle_result.trace
            )
        if self.result_kind == "sensor_comparison":
            sensor_comparison_result = CompareSensorCyclesResult.model_validate(payload).root
            if not sensor_comparison_result.ok:
                return BranchOutcome(
                    evidence=[],
                    trace=sensor_comparison_result.trace,
                    error=StructuredError(
                        code=sensor_comparison_result.error.code,
                        message=sensor_comparison_result.error.message,
                        retryable=sensor_comparison_result.error.retryable,
                        request_id=request_id,
                    ),
                )
            return BranchOutcome(
                evidence=list(sensor_comparison_result.comparison.citations),
                trace=sensor_comparison_result.trace,
            )
        fault_case_result = SearchFaultCasesResult.model_validate(payload).root
        if not fault_case_result.ok:
            return BranchOutcome(
                evidence=[],
                trace=fault_case_result.trace,
                error=StructuredError(
                    code=fault_case_result.error.code,
                    message=fault_case_result.error.message,
                    retryable=fault_case_result.error.retryable,
                    request_id=request_id,
                ),
            )
        return BranchOutcome(
            evidence=[case.citation for case in fault_case_result.cases],
            trace=fault_case_result.trace,
        )


@dataclass(frozen=True)
class UnsupportedSafeToolBranchAdapter:
    """Normalize unconfigured raw tools through the ordinary branch error boundary."""

    tool_name: str

    def execute(self, *, query: str, request_id: str) -> BranchOutcome:
        del query, request_id
        raise TypeError(f"{self.tool_name} requires an explicit SafeToolBranchAdapter")


BranchSource: TypeAlias = Callable[[str], EvidenceList] | SafeStructuredTool | BranchAdapter
ManualSearch: TypeAlias = BranchSource
SensorSearch: TypeAlias = BranchSource
CaseSearch: TypeAlias = BranchSource
EvidenceSufficiencyCheck = Callable[
    [list[ManualCitation], list[SensorCitation], list[SyntheticCitation]], bool
]


def _default_tool_arguments(query: str, request_id: str) -> dict[str, object]:
    return {"query": query, "request_id": request_id}


def _coerce_branch_adapter(source: BranchSource, branch_name: str) -> BranchAdapter:
    if isinstance(source, BranchAdapter):
        return source
    if isinstance(source, SafeStructuredTool):
        kind_by_tool_name = {
            "search_manual_knowledge": "manual",
            "search_fault_cases": "case",
        }
        result_kind = kind_by_tool_name.get(source.name)
        if result_kind is None:
            return UnsupportedSafeToolBranchAdapter(tool_name=source.name)
        return SafeToolBranchAdapter(
            tool=source,
            result_kind=cast(Literal["manual", "case"], result_kind),
            argument_builder=_default_tool_arguments,
        )
    return CallableBranchAdapter(source)


def _trace(
    *,
    request_id: str,
    node: str,
    tool: str,
    status: Literal["success", "failure", "skipped"],
    started: float,
    evidence_count: int = 0,
    error_code: str | None = None,
) -> TraceEvent:
    return TraceEvent(
        request_id=request_id,
        node=node,
        action="evidence_retrieval" if node.startswith("retrieve") else node,
        status=status,
        duration_ms=max(0.0, (perf_counter() - started) * 1_000),
        tool=tool,
        evidence_count=evidence_count,
        parameter_summary={},
        error_code=error_code,
    )


def _error(request_id: str, code: str, message: str, *, retryable: bool = False) -> StructuredError:
    return StructuredError(code=code, message=message, retryable=retryable, request_id=request_id)


def initialize(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    """Initialize bounded state once; it never derives data from prior sessions."""

    user_query = str(state["user_query"])
    conversation_id = str(state["conversation_id"])
    return {
        "user_query": user_query,
        "conversation_id": conversation_id,
        "request_id": f"wf-{uuid4().hex}",
        "retrieval_query": user_query.strip(),
        "retry_count": 0,
        "http_retry_count": 0,
        "retrieval_new_evidence_count": 0,
        "documents": cast(list[ManualCitation], Overwrite(value=[])),
        "sensor_evidence": cast(list[SensorCitation], Overwrite(value=[])),
        "fault_cases": cast(list[SyntheticCitation], Overwrite(value=[])),
        "traces": cast(list[TraceEvent], Overwrite(value=[])),
        "errors": cast(list[StructuredError], Overwrite(value=[])),
    }


def safety_precheck(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    assessment = classify_input(str(state["user_query"]))
    return {
        "safety_assessment": assessment,
        "safety_decision": "RESTRICTED" if assessment.prohibited else "CONTINUE",
    }


def intent_router(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    intent = route_intent(str(state["user_query"]))
    return {"intent": intent.value}


def _is_retryable_dependency_error(error: Exception) -> bool:
    return isinstance(error, (ConnectionError, TimeoutError)) or bool(
        getattr(error, "retryable", False)
    )


def _validate_branch_evidence(name: str, evidence: object) -> EvidenceList:
    """Reject malformed adapter output before it can enter graph reducers."""

    expected_type: type[ManualCitation] | type[SensorCitation] | type[SyntheticCitation]
    if name == "manual":
        expected_type = ManualCitation
    elif name == "sensor":
        expected_type = SensorCitation
    elif name == "case":
        expected_type = SyntheticCitation
    else:
        raise TypeError("unknown evidence branch")
    if not isinstance(evidence, list) or any(
        not isinstance(citation, expected_type)
        or not isinstance(citation.citation_id, str)
        or not citation.citation_id.strip()
        for citation in evidence
    ):
        raise TypeError("branch returned malformed evidence")
    return cast(EvidenceList, evidence)


def _run_branch(
    *,
    request_id: str,
    name: str,
    query: str,
    adapter: BranchAdapter,
) -> tuple[str, EvidenceList, TraceEvent, StructuredError | None, bool]:
    started = perf_counter()
    try:
        outcome = adapter.execute(query=query, request_id=request_id)
        evidence = _validate_branch_evidence(name, outcome.evidence)
        if outcome.error is not None:
            return (
                name,
                [],
                outcome.trace
                or _trace(
                    request_id=request_id,
                    node=f"retrieve_{name}",
                    tool=name,
                    status="failure",
                    started=started,
                    error_code=outcome.error.code,
                ),
                outcome.error,
                outcome.error.retryable,
            )
    except Exception as error:
        code = f"{name.upper()}_BRANCH_ERROR"
        retryable = _is_retryable_dependency_error(error)
        return (
            name,
            [],
            _trace(
                request_id=request_id,
                node=f"retrieve_{name}",
                tool=name,
                status="failure",
                started=started,
                error_code=code,
            ),
            _error(request_id, code, "证据分支暂时不可用。", retryable=retryable),
            retryable,
        )
    return (
        name,
        evidence,
        outcome.trace
        or _trace(
            request_id=request_id,
            node=f"retrieve_{name}",
            tool=name,
            status="success",
            started=started,
            evidence_count=len(evidence),
        ),
        None,
        False,
    )


def build_retrieve_evidence(
    *,
    manual_search: ManualSearch,
    sensor_search: SensorSearch,
    case_search: CaseSearch,
    max_http_retries: int,
) -> Callable[[EvidenceWorkflowState], EvidenceWorkflowState]:
    """Create retrieval node; fault branches execute concurrently on the first pass."""

    def retrieve_evidence(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
        request_id = str(state["request_id"])
        query = str(state["retrieval_query"])
        is_initial_fault_pass = (
            state.get("intent") == Intent.FAULT_DIAGNOSIS.value and state["retry_count"] == 0
        )
        branches = [("manual", _coerce_branch_adapter(manual_search, "manual"))]
        if is_initial_fault_pass:
            branches.extend(
                (
                    ("sensor", _coerce_branch_adapter(sensor_search, "sensor")),
                    ("case", _coerce_branch_adapter(case_search, "case")),
                )
            )

        if len(branches) == 1:
            results = [
                _run_branch(
                    request_id=request_id, name="manual", query=query, adapter=branches[0][1]
                )
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=len(branches), thread_name_prefix="evidence"
            ) as pool:
                futures = {
                    name: pool.submit(
                        _run_branch,
                        request_id=request_id,
                        name=name,
                        query=query,
                        adapter=search,
                    )
                    for name, search in branches
                }
                results = [futures[name].result() for name, _ in branches]

        traces: list[TraceEvent] = []
        errors: list[StructuredError] = []
        http_retries_used = 0
        retries_remaining = max(0, max_http_retries - state["http_retry_count"])
        final_results: list[tuple[str, EvidenceList, TraceEvent, StructuredError | None, bool]] = []
        for name, evidence, trace, error, retryable in results:
            traces.append(trace)
            while error is not None and retryable and retries_remaining:
                retries_remaining -= 1
                http_retries_used += 1
                name, evidence, trace, error, retryable = _run_branch(
                    request_id=request_id,
                    name=name,
                    query=query,
                    adapter=dict(branches)[name],
                )
                traces.append(trace)
            final_results.append((name, evidence, trace, error, retryable))

        updates: EvidenceWorkflowState = {}
        existing_manual_ids = {citation.citation_id for citation in state.get("documents", [])}
        existing_sensor_ids = {
            citation.citation_id for citation in state.get("sensor_evidence", [])
        }
        existing_case_ids = {citation.citation_id for citation in state.get("fault_cases", [])}
        new_evidence_count = 0
        for name, evidence, _, error, _ in final_results:
            cast_evidence = evidence
            if name == "manual":
                updates["documents"] = cast(list[ManualCitation], cast_evidence)
                new_evidence_count += _count_new_citations(
                    cast(list[ManualCitation], cast_evidence), existing_manual_ids
                )
            elif name == "sensor":
                updates["sensor_evidence"] = cast(list[SensorCitation], cast_evidence)
                new_evidence_count += _count_new_citations(
                    cast(list[SensorCitation], cast_evidence), existing_sensor_ids
                )
            else:
                updates["fault_cases"] = cast(list[SyntheticCitation], cast_evidence)
                new_evidence_count += _count_new_citations(
                    cast(list[SyntheticCitation], cast_evidence), existing_case_ids
                )
            if error is not None:
                errors.append(error)
        updates["traces"] = traces
        updates["errors"] = errors
        updates["http_retry_count"] = state["http_retry_count"] + http_retries_used
        updates["retrieval_new_evidence_count"] = new_evidence_count
        return updates

    return retrieve_evidence


def _count_new_citations(
    citations: list[ManualCitation] | list[SensorCitation] | list[SyntheticCitation],
    previous_ids: set[str],
) -> int:
    seen_ids = set(previous_ids)
    count = 0
    for citation in citations:
        if citation.citation_id not in seen_ids:
            seen_ids.add(citation.citation_id)
            count += 1
    return count


def build_evaluate_evidence(
    evidence_is_sufficient: EvidenceSufficiencyCheck,
) -> Callable[[EvidenceWorkflowState], EvidenceWorkflowState]:
    """Decide whether evidence is sufficient without assigning the public status."""

    def evaluate_evidence(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
        documents = state.get("documents", [])
        sensors = state.get("sensor_evidence", [])
        cases = state.get("fault_cases", [])
        if state["retrieval_new_evidence_count"] == 0:
            decision = "INSUFFICIENT_EVIDENCE"
        elif evidence_is_sufficient(documents, sensors, cases):
            decision = "COMPLETED"
        elif state["retry_count"] < 2 and str(state["user_query"]).strip():
            decision = "REWRITE"
        else:
            decision = "INSUFFICIENT_EVIDENCE"
        return {"evidence_decision": decision}

    return evaluate_evidence


def rewrite_retrieval_query(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    """Advance only the retrieval query; the original user query is immutable."""

    retry_count = state["retry_count"]
    original = str(state["user_query"]).strip()
    current = str(state["retrieval_query"]).strip()
    if retry_count >= 2 or not original:
        return {}
    candidate = f"{original} 维护故障检索角度 {retry_count + 1}".strip()
    if not candidate or candidate == current:
        return {}
    return {
        "retrieval_query": candidate,
        "retry_count": retry_count + 1,
    }


def finalize_status(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    """The only node allowed to expose a public workflow status."""

    if state.get("safety_decision") == "RESTRICTED":
        status = "SAFETY_RESTRICTED"
    else:
        status = str(state.get("evidence_decision", "INSUFFICIENT_EVIDENCE"))
    return {"workflow_status": status}


def safety_review(state: EvidenceWorkflowState) -> EvidenceWorkflowState:
    """Review a fixed public summary before the graph returns it."""

    assessment = state.get("safety_assessment")
    if not isinstance(assessment, SafetyAssessment):
        assessment = classify_input("")
    if state.get("workflow_status") == "SAFETY_RESTRICTED":
        answer = "该请求涉及禁止绕过安全保护, 无法提供操作指导。"
    elif state.get("workflow_status") == "INSUFFICIENT_EVIDENCE":
        answer = "当前可验证证据不足, 建议由具备资质的现场人员按有效规程复核。"
    else:
        answer = "已完成基于可验证证据的辅助分析, 请结合现场有效规程和持证人员判断。"
    reviewed = review_output(answer, input_assessment=assessment)
    trace = _trace(
        request_id=str(state["request_id"]),
        node="safety_review",
        tool="safety_review",
        status="success",
        started=perf_counter(),
    )
    return {"answer": reviewed.answer, "safety_output": reviewed, "traces": [trace]}
