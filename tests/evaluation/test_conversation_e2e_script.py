from __future__ import annotations

from scripts.run_phase10_conversation_e2e_ragas import (
    console_summary,
    semantic_preflight_block_reason,
)


def test_console_summary_is_ascii_safe_for_windows_gbk_console() -> None:
    text = console_summary({"status": "BLOCKED", "reason": "泵的 Φ 值"})

    assert text.isascii()
    assert '"status": "BLOCKED"' in text


def test_semantic_preflight_block_reason_preserves_each_failed_layer() -> None:
    reason = semantic_preflight_block_reason({
        "status": "BLOCKED",
        "components": {
            "chat": {"status": "BLOCKED", "reason_code": "chat_provider_error", "reason": "HTTP 500"},
            "embedding": {"status": "READY"},
            "faithfulness": {"status": "BLOCKED", "reason_code": "faithfulness_metric_error", "reason": "metric failed"},
            "response_relevancy": {"status": "READY"},
        },
    })

    assert reason == "chat_provider_error: HTTP 500; faithfulness_metric_error: metric failed"
