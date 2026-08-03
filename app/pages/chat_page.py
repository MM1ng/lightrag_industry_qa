"""Small composition boundary for the chat page.

The legacy entry point still owns navigation while rendering logic migrates
incrementally into this module.
"""

from __future__ import annotations

from collections.abc import Callable


def render_chat_page(render_fn: Callable[[], None]) -> None:
    render_fn()

