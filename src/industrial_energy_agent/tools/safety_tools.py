"""Small deterministic safety-requirement tool (not the Task 12 safety router)."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, RootModel

from industrial_energy_agent.domain.enums import RiskLevel
from industrial_energy_agent.domain.models import TraceEvent
from industrial_energy_agent.tools.common import (
    SafeStructuredTool,
    ToolFailure,
    ToolInputModel,
    ToolModel,
    build_safe_structured_tool,
    dump_result,
    make_error,
    make_trace,
    new_request_id,
    started_at,
)


class SafetyRequirements(ToolModel):
    equipment: str
    activity: str
    risk_level: RiskLevel
    approval_required: bool
    items: tuple[str, ...]
    limitations: tuple[str, ...]


class SafetyRuleProviderBoundary(Protocol):
    def get_requirements(
        self,
        *,
        equipment: str,
        activity: str,
        risk_level: RiskLevel,
    ) -> SafetyRequirements | None: ...


class DeterministicSafetyRuleProvider:
    """Return a narrow fixed checklist without performing routing or authorization."""

    def get_requirements(
        self,
        *,
        equipment: str,
        activity: str,
        risk_level: RiskLevel,
    ) -> SafetyRequirements:
        items = [
            "仅由具备相应资质和现场授权的人员开展检查。",
            "遵循现场有效规程并确认上锁挂牌、能量隔离和泄压边界。",
            "确认个人防护用品、作业许可和现场监护要求。",
        ]
        if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            items.append("开始任何现场处置前必须完成正式风险审查和人工审批。")
        return SafetyRequirements(
            equipment=equipment,
            activity=activity,
            risk_level=risk_level,
            approval_required=risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL},
            items=tuple(items),
            limitations=("本清单不能替代现场规程、操作票或持证人员判断。",),
        )


class GetSafetyRequirementsInput(ToolInputModel):
    equipment: str = Field(min_length=1, max_length=500)
    activity: str = Field(min_length=1, max_length=2_000)
    risk_level: RiskLevel = RiskLevel.MEDIUM


class GetSafetyRequirementsSuccess(ToolModel):
    ok: Literal[True] = True
    requirements: SafetyRequirements
    trace: TraceEvent


class GetSafetyRequirementsFailure(ToolFailure):
    pass


class GetSafetyRequirementsResult(
    RootModel[GetSafetyRequirementsSuccess | GetSafetyRequirementsFailure]
):
    pass


class GetSafetyRequirementsService:
    def __init__(self, provider: SafetyRuleProviderBoundary) -> None:
        self._provider = provider

    def execute(self, args: GetSafetyRequirementsInput) -> GetSafetyRequirementsResult:
        started = started_at()
        if not args.equipment.strip() or not args.activity.strip():
            return self._failure(
                args,
                started,
                "INVALID_INPUT",
                "设备和活动描述不能为空。",
            )
        try:
            requirements = self._provider.get_requirements(
                equipment=args.equipment,
                activity=args.activity,
                risk_level=args.risk_level,
            )
        except Exception:
            return self._failure(
                args,
                started,
                "SAFETY_DEPENDENCY_ERROR",
                "安全要求暂时不可用。",
                retryable=True,
            )
        if requirements is None:
            return self._failure(
                args,
                started,
                "SAFETY_REQUIREMENTS_NOT_FOUND",
                "未找到适用的确定性安全要求。",
            )
        success = GetSafetyRequirementsSuccess(
            requirements=requirements,
            trace=make_trace(
                request_id=args.request_id,
                tool="get_safety_requirements",
                started=started,
                status="success",
                evidence_count=len(requirements.items),
                parameter_summary={
                    "equipment_length": len(args.equipment),
                    "activity_length": len(args.activity),
                    "risk_level": args.risk_level.value,
                },
            ),
        )
        return GetSafetyRequirementsResult(root=success)

    @staticmethod
    def _failure(
        args: GetSafetyRequirementsInput,
        started: float,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> GetSafetyRequirementsResult:
        failure = GetSafetyRequirementsFailure(
            error=make_error(code, message, retryable=retryable),
            trace=make_trace(
                request_id=args.request_id,
                tool="get_safety_requirements",
                started=started,
                status="failure",
                parameter_summary={
                    "equipment_length": len(args.equipment),
                    "activity_length": len(args.activity),
                    "risk_level": args.risk_level.value,
                },
                error_code=code,
            ),
        )
        return GetSafetyRequirementsResult(root=failure)


def build_get_safety_requirements_tool(
    provider: SafetyRuleProviderBoundary,
) -> SafeStructuredTool:
    service = GetSafetyRequirementsService(provider)

    def get_safety_requirements(
        equipment: str,
        activity: str,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        request_id: str = "",
    ) -> dict[str, Any]:
        args = GetSafetyRequirementsInput(
            equipment=equipment,
            activity=activity,
            risk_level=risk_level,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=get_safety_requirements,
        name="get_safety_requirements",
        description="返回确定性的工业安全检查清单; 不执行操作, 也不代表审批。",
        args_schema=GetSafetyRequirementsInput,
    )
