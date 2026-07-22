"""Dependency-injected LangGraph for bounded industrial evidence retrieval."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from industrial_energy_agent.agents.state import EvidenceWorkflowState
from industrial_energy_agent.workflow.nodes import (
    CaseSearch,
    EvidenceSufficiencyCheck,
    ManualSearch,
    SensorSearch,
    build_evaluate_evidence,
    build_retrieve_evidence,
    finalize_status,
    initialize,
    intent_router,
    rewrite_retrieval_query,
    safety_precheck,
    safety_review,
)
from industrial_energy_agent.workflow.routing import route_after_evaluation, route_after_precheck


@dataclass(frozen=True)
class WorkflowDependencies:
    """Explicit read-only evidence dependencies; callers own all I/O choices."""

    manual_search: ManualSearch
    sensor_search: SensorSearch
    case_search: CaseSearch
    evidence_is_sufficient: EvidenceSufficiencyCheck = lambda documents, sensors, cases: bool(
        documents or sensors or cases
    )
    max_http_retries: int = 2


@dataclass(frozen=True)
class _TypedEvidenceNode:
    """Explicit LangGraph node wrapper retaining the state/update TypedDict type."""

    delegate: Callable[[EvidenceWorkflowState], EvidenceWorkflowState]

    def __call__(self, state: EvidenceWorkflowState) -> EvidenceWorkflowState:
        return self.delegate(state)


def build_evidence_graph(
    dependencies: WorkflowDependencies,
) -> CompiledStateGraph[EvidenceWorkflowState, None, EvidenceWorkflowState, EvidenceWorkflowState]:
    """Compile the offline-testable workflow with at most three manual searches."""

    graph: StateGraph[EvidenceWorkflowState, None, EvidenceWorkflowState, EvidenceWorkflowState] = (
        StateGraph(EvidenceWorkflowState)
    )
    graph.add_node("initialize", initialize)
    graph.add_node("safety_precheck", safety_precheck)
    graph.add_node("intent_router", intent_router)
    graph.add_node(
        "retrieve_evidence",
        _TypedEvidenceNode(
            build_retrieve_evidence(
                manual_search=dependencies.manual_search,
                sensor_search=dependencies.sensor_search,
                case_search=dependencies.case_search,
                max_http_retries=dependencies.max_http_retries,
            )
        ),
    )
    graph.add_node(
        "evaluate_evidence",
        _TypedEvidenceNode(build_evaluate_evidence(dependencies.evidence_is_sufficient)),
    )
    graph.add_node("rewrite", rewrite_retrieval_query)
    graph.add_node("finalize_status", finalize_status)
    graph.add_node("safety_review", safety_review)

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "safety_precheck")
    graph.add_conditional_edges("safety_precheck", route_after_precheck)
    graph.add_edge("intent_router", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "evaluate_evidence")
    graph.add_conditional_edges("evaluate_evidence", route_after_evaluation)
    graph.add_edge("rewrite", "retrieve_evidence")
    graph.add_edge("finalize_status", "safety_review")
    graph.add_edge("safety_review", END)
    return graph.compile()
