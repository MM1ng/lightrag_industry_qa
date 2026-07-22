"""Deterministic intent routing with an intentionally conservative fallback."""

from __future__ import annotations

from industrial_energy_agent.domain.enums import Intent

_SIGNALS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.WORK_ORDER_DRAFT, ("工单", "起草", "草拟")),
    (Intent.FAULT_DIAGNOSIS, ("故障", "诊断", "异常", "压力低", "压力偏低")),
    (Intent.SENSOR_QUERY, ("传感器", "周期", "测点", "趋势", "压力数据")),
    (Intent.SAFETY_QUERY, ("安全", "风险", "上锁挂牌", "防护")),
    (Intent.OPERATION_PROCEDURE, ("操作规程", "操作步骤", "停机", "启动", "程序")),
    (Intent.EQUIPMENT_QA, ("额定", "参数", "原理", "是什么", "型号", "液压泵", "轴承")),
)


def classify_intent(query: str) -> Intent:
    """Return a public intent only when a deterministic signal is present."""

    normalized = query.strip().casefold()
    if len(normalized) < 2:
        return Intent.UNKNOWN
    for intent, signals in _SIGNALS:
        if any(signal in normalized for signal in signals):
            return intent
    return Intent.UNKNOWN
