"""Process-local operational metrics with stable, secret-free dimensions."""

from __future__ import annotations

import hashlib
import os
import socket
from collections import Counter
from threading import Lock
from typing import Any


def _stable_instance_id() -> str:
    configured = os.environ.get("INSTANCE_ID", "").strip()
    value = configured or f"{socket.gethostname()}:{os.getpid()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class OperationalMetrics:
    def __init__(self) -> None:
        self.instance_id = _stable_instance_id()
        self._counters: Counter[str] = Counter()
        self._gauges: dict[str, Any] = {}
        self._lock = Lock()

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def set(self, name: str, value: Any) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "instance_id": self.instance_id,
                "counters": dict(sorted(self._counters.items())),
                "gauges": dict(sorted(self._gauges.items())),
            }


operational_metrics = OperationalMetrics()
