"""Graph page composition boundary."""

from __future__ import annotations

from collections.abc import Callable


def render_graph_page(render_fn: Callable[[], None]) -> None:
    render_fn()

