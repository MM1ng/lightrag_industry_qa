from __future__ import annotations

import json

from industrial_energy_agent.domain.enums import RiskLevel
from industrial_energy_agent.tools.safety_tools import (
    DeterministicSafetyRuleProvider,
    GetSafetyRequirementsInput,
    GetSafetyRequirementsResult,
    SafetyRequirements,
    build_get_safety_requirements_tool,
)


class EmptySafetyRuleProvider:
    def get_requirements(
        self,
        *,
        equipment: str,
        activity: str,
        risk_level: RiskLevel,
    ) -> SafetyRequirements | None:
        return None


class FailingSafetyRuleProvider:
    def get_requirements(
        self,
        *,
        equipment: str,
        activity: str,
        risk_level: RiskLevel,
    ) -> SafetyRequirements | None:
        raise RuntimeError('Traceback File "D:\\private\\safety.py" SERVICE_TOKEN=safety-secret')


def test_get_safety_requirements_returns_deterministic_high_risk_rules() -> None:
    tool = build_get_safety_requirements_tool(DeterministicSafetyRuleProvider())

    result = tool.invoke(
        {
            "equipment": "PUMP-001",
            "activity": "检查液压泵",
            "risk_level": "HIGH",
            "request_id": "req-safety-success",
        }
    )

    parsed = GetSafetyRequirementsResult.model_validate(result)
    assert parsed.root.ok is True
    assert result["requirements"]["risk_level"] == "HIGH"
    assert result["requirements"]["approval_required"] is True
    assert any("授权" in item for item in result["requirements"]["items"])
    assert tool.name == "get_safety_requirements"
    assert tool.args_schema is GetSafetyRequirementsInput


def test_get_safety_requirements_returns_not_found() -> None:
    tool = build_get_safety_requirements_tool(EmptySafetyRuleProvider())

    result = tool.invoke(
        {
            "equipment": "UNKNOWN",
            "activity": "查询",
            "request_id": "req-safety-empty",
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_REQUIREMENTS_NOT_FOUND"


def test_get_safety_requirements_returns_structured_invalid_input() -> None:
    tool = build_get_safety_requirements_tool(DeterministicSafetyRuleProvider())

    result = tool.invoke(
        {"equipment": "PUMP-001", "activity": "  ", "request_id": "req-safety-invalid"}
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_get_safety_requirements_sanitizes_dependency_failure() -> None:
    tool = build_get_safety_requirements_tool(FailingSafetyRuleProvider())

    result = tool.invoke(
        {
            "equipment": "PUMP-001",
            "activity": "检查",
            "request_id": "req-safety-error",
        }
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_DEPENDENCY_ERROR"
    assert "requirements" not in result
    assert "Traceback" not in rendered
    assert "safety-secret" not in rendered
    assert "safety.py" not in rendered
