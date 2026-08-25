"""Pure UI labels for the Phase 11 answer feedback controls."""

from __future__ import annotations

FEEDBACK_REASON_LABELS: tuple[tuple[str, str], ...] = (
    ("答案不正确", "answer_incorrect"),
    ("引用不支持答案", "citation_unsupported"),
    ("答案不完整", "answer_incomplete"),
    ("没有找到答案", "answer_not_found"),
    ("本来有答案但错误拒答", "false_refusal"),
    ("回答了本应拒答或证据不足的问题", "unsafe_or_unnecessary_answer"),
    ("响应太慢", "response_too_slow"),
    ("其他", "other"),
)


def feedback_reason_values() -> tuple[str, ...]:
    return tuple(value for _, value in FEEDBACK_REASON_LABELS)


def feedback_reason_label(value: str) -> str:
    for label, reason in FEEDBACK_REASON_LABELS:
        if reason == value:
            return label
    return value
