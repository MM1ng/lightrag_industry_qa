"""Handler registry: maps TaskType → LifecycleTaskHandler.

Each handler is a callable that receives TaskExecutionContext and returns
TaskExecutionResult.  The executor looks up the handler by task_type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from industrial_rag.db.models import TaskType
from industrial_rag.services.task_context import TaskExecutionContext

LifecycleTaskHandler = Callable[[TaskExecutionContext], Any]  # returns coroutine → TaskExecutionResult


class TaskHandlerRegistry:
    """Maps task types to their handler coroutines."""

    def __init__(self) -> None:
        self._handlers: dict[TaskType, LifecycleTaskHandler] = {}

    def register(self, task_type: TaskType, handler: LifecycleTaskHandler) -> None:
        self._handlers[task_type] = handler

    def get(self, task_type: TaskType) -> LifecycleTaskHandler | None:
        return self._handlers.get(task_type)


# ---------------------------------------------------------------------------
# Built-in registrations happen at import time (handlers import this module)
# ---------------------------------------------------------------------------

_builtin_registry = TaskHandlerRegistry()


def register_handler(task_type: TaskType):
    """Decorator to register a handler function."""

    def decorator(fn: LifecycleTaskHandler) -> LifecycleTaskHandler:
        _builtin_registry.register(task_type, fn)
        return fn

    return decorator


def get_builtin_registry() -> TaskHandlerRegistry:
    return _builtin_registry
