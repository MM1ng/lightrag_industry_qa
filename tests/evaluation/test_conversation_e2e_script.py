from __future__ import annotations

from scripts.run_phase10_conversation_e2e_ragas import console_summary


def test_console_summary_is_ascii_safe_for_windows_gbk_console() -> None:
    text = console_summary({"status": "BLOCKED", "reason": "泵的 Φ 值"})

    assert text.isascii()
    assert '"status": "BLOCKED"' in text
