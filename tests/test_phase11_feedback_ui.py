from __future__ import annotations

from app.feedback_ui import FEEDBACK_REASON_LABELS, feedback_reason_label, feedback_reason_values


def test_feedback_ui_exposes_exact_fixed_negative_reasons() -> None:
    assert feedback_reason_values() == (
        "answer_incorrect",
        "citation_unsupported",
        "answer_incomplete",
        "answer_not_found",
        "false_refusal",
        "unsafe_or_unnecessary_answer",
        "response_too_slow",
        "other",
    )
    assert feedback_reason_label("other") == "其他"
    assert len(FEEDBACK_REASON_LABELS) == 8
