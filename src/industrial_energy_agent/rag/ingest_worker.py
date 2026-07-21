"""Small worker loop around the protected ingestion application service."""

from __future__ import annotations

import time

from industrial_energy_agent.persistence.ingest_job_repository import IngestJob
from industrial_energy_agent.rag.ingestion import IngestionService


def run_worker(
    service: IngestionService,
    *,
    once: bool,
    idle_seconds: float = 1.0,
) -> IngestJob | None:
    """Process one job or keep polling without bypassing the application service."""

    if idle_seconds < 0:
        raise ValueError("idle_seconds must be non-negative")
    while True:
        result = service.run_once()
        if result is None:
            result = service.recover_next_expired()
        if once:
            return result
        if result is None:
            time.sleep(idle_seconds)
