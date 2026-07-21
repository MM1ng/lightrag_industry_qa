"""Stable name registry for the six injected LangChain tools."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from langchain_core.tools import BaseTool

from industrial_energy_agent.tools.fault_case_tools import (
    FaultCaseRepositoryBoundary,
    build_search_fault_cases_tool,
)
from industrial_energy_agent.tools.knowledge_tools import (
    KnowledgeRAGBoundary,
    build_search_manual_knowledge_tool,
)
from industrial_energy_agent.tools.safety_tools import (
    SafetyRuleProviderBoundary,
    build_get_safety_requirements_tool,
)
from industrial_energy_agent.tools.sensor_tools import (
    SensorRepositoryBoundary,
    build_compare_sensor_cycles_tool,
    build_query_sensor_cycle_tool,
)
from industrial_energy_agent.tools.work_order_tools import (
    DiagnosisRepositoryBoundary,
    WorkOrderRepositoryBoundary,
    build_create_work_order_draft_tool,
)


class ToolRegistry:
    """Immutable lookup that rejects unstable duplicate public names."""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        by_name = {tool.name: tool for tool in tools}
        if len(by_name) != len(tools):
            raise ValueError("duplicate public tool name")
        self._tools = tuple(tools)
        self._by_name = by_name

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def get(self, name: str) -> BaseTool:
        return self._by_name[name]

    @property
    def tools(self) -> tuple[BaseTool, ...]:
        return self._tools


def build_tool_registry(
    *,
    rag: KnowledgeRAGBoundary,
    sensors: SensorRepositoryBoundary,
    fault_cases: FaultCaseRepositoryBoundary,
    safety_rules: SafetyRuleProviderBoundary,
    diagnoses: DiagnosisRepositoryBoundary,
    work_orders: WorkOrderRepositoryBoundary,
    conversation_id: str,
) -> ToolRegistry:
    """Build exactly the six public tools from caller-owned dependencies."""

    return ToolRegistry(
        (
            build_search_manual_knowledge_tool(rag),
            build_query_sensor_cycle_tool(sensors),
            build_compare_sensor_cycles_tool(sensors),
            build_search_fault_cases_tool(fault_cases),
            build_get_safety_requirements_tool(safety_rules),
            build_create_work_order_draft_tool(
                diagnoses,
                work_orders,
                conversation_id=conversation_id,
            ),
        )
    )
