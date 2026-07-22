from __future__ import annotations

from dataclasses import dataclass

import pytest

from industrial_energy_agent.domain.models import ManualCitation
from industrial_energy_agent.workflow.graph import WorkflowDependencies, build_evidence_graph


@dataclass
class FakeRag:
    always_insufficient: bool = False
    repeat_evidence: bool = False
    search_calls: int = 0

    def search(self, query: str) -> list[ManualCitation]:
        self.search_calls += 1
        if not self.always_insufficient:
            return []
        evidence_id = "manual-repeat" if self.repeat_evidence else f"manual-{self.search_calls}"
        return [
            ManualCitation(
                citation_id=evidence_id,
                document_title="轴承维护手册",
                page_number=1,
                chunk_id=evidence_id,
                excerpt="已验证的维护证据。",
            )
        ]


@dataclass
class Fakes:
    rag: FakeRag

    def dependencies(self) -> WorkflowDependencies:
        return WorkflowDependencies(
            manual_search=self.rag.search,
            sensor_search=lambda query: [],
            case_search=lambda query: [],
            evidence_is_sufficient=lambda documents, sensors, cases: (
                not self.rag.always_insufficient
            ),
        )


@pytest.fixture
def fakes() -> Fakes:
    return Fakes(rag=FakeRag())


@pytest.fixture
def graph(fakes: Fakes):
    return build_evidence_graph(fakes.dependencies())


def test_initial_query_plus_two_rewrites_stops_after_three_searches(graph, fakes: Fakes) -> None:
    fakes.rag.always_insufficient = True

    result = graph.invoke({"user_query": "轴承问题", "conversation_id": "c1"})

    assert fakes.rag.search_calls == 3
    assert result["retry_count"] == 2
    assert result["workflow_status"] == "INSUFFICIENT_EVIDENCE"
    assert len(result["traces"]) == 4


def test_rewrite_never_changes_original_query(graph, fakes: Fakes) -> None:
    original = "直接切断电源并拆开泵体"

    result = graph.invoke({"user_query": original, "conversation_id": "c1"})

    assert result["user_query"] == original


def test_initialize_preserves_the_exact_original_user_query(graph, fakes: Fakes) -> None:
    original = "  轴承问题  "

    result = graph.invoke({"user_query": original, "conversation_id": " c1 "})

    assert result["user_query"] == original
    assert result["retrieval_query"] == "轴承问题"


def test_empty_manual_result_stops_without_semantic_rewrite(graph, fakes: Fakes) -> None:
    result = graph.invoke({"user_query": "轴承问题", "conversation_id": "c1"})

    assert fakes.rag.search_calls == 1
    assert result["retry_count"] == 0
    assert result["workflow_status"] == "INSUFFICIENT_EVIDENCE"


def test_repeated_manual_evidence_stops_when_a_rewrite_adds_nothing(graph, fakes: Fakes) -> None:
    fakes.rag.always_insufficient = True
    fakes.rag.repeat_evidence = True

    result = graph.invoke({"user_query": "轴承问题", "conversation_id": "c1"})

    assert fakes.rag.search_calls == 2
    assert result["retry_count"] == 1
    assert len(result["documents"]) == 1
    assert result["workflow_status"] == "INSUFFICIENT_EVIDENCE"


@dataclass
class RetryableRag:
    failures_before_success: int = 1
    search_calls: int = 0

    def search(self, query: str) -> list[ManualCitation]:
        self.search_calls += 1
        if self.search_calls <= self.failures_before_success:
            raise TimeoutError("temporary dependency failure")
        return [
            ManualCitation(
                citation_id=f"retryable-manual-{self.search_calls}",
                document_title="轴承维护手册",
                page_number=1,
                chunk_id=f"retryable-manual-{self.search_calls}",
                excerpt="已验证的维护证据。",
            )
        ]


def test_http_retries_are_bounded_and_independent_from_semantic_rewrites() -> None:
    rag = RetryableRag(failures_before_success=1)
    graph = build_evidence_graph(
        WorkflowDependencies(
            manual_search=rag.search,
            sensor_search=lambda query: [],
            case_search=lambda query: [],
            evidence_is_sufficient=lambda documents, sensors, cases: False,
            max_http_retries=2,
        )
    )

    result = graph.invoke({"user_query": "轴承问题", "conversation_id": "c1"})

    assert rag.search_calls == 4
    assert result["http_retry_count"] == 1
    assert result["retry_count"] == 2
    assert result["workflow_status"] == "INSUFFICIENT_EVIDENCE"
