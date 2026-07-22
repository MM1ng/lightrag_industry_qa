from __future__ import annotations

import pytest

from industrial_energy_agent.domain.enums import Intent
from industrial_energy_agent.workflow.routing import route_intent


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("液压泵的额定压力是多少?", Intent.EQUIPMENT_QA),
        ("如何执行液压泵的停机操作规程?", Intent.OPERATION_PROCEDURE),
        ("更换滤芯时有哪些安全要求?", Intent.SAFETY_QUERY),
        ("读取 12 号周期的传感器压力", Intent.SENSOR_QUERY),
        ("泵出口压力低, 帮我诊断故障", Intent.FAULT_DIAGNOSIS),
        ("为泵压力异常起草工单", Intent.WORK_ORDER_DRAFT),
        ("嗯", Intent.UNKNOWN),
    ],
)
def test_deterministic_router_covers_public_intents_and_low_confidence_unknown(
    query: str, expected: Intent
) -> None:
    assert route_intent(query) is expected


def test_router_does_not_guess_when_query_has_no_confident_signal() -> None:
    assert route_intent("请处理一下") is Intent.UNKNOWN
