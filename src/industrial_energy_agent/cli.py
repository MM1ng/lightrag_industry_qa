"""Command-line entry points that reuse EnergyOps application services."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from industrial_energy_agent import __version__
from industrial_energy_agent.config.settings import Settings
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.ingest_job_repository import IngestJobRepository
from industrial_energy_agent.rag.ingest_worker import run_worker
from industrial_energy_agent.rag.ingestion import DocumentRegistry, IngestionService
from industrial_energy_agent.rag.lightrag_adapter import LightRAGRestAdapter


@dataclass(frozen=True, slots=True)
class IngestionRuntime:
    """Resources owned by one CLI invocation."""

    service: IngestionService
    adapter: LightRAGRestAdapter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="energyops",
        description="EnergyOps Copilot safety-first industrial decision-support service",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command")
    worker = commands.add_parser("ingest-worker", help="process protected ingestion jobs")
    worker.add_argument("--once", action="store_true", help="process at most one job")
    worker.add_argument("--idle-seconds", type=float, default=1.0)
    reconcile = commands.add_parser(
        "reconcile-ingest",
        help="reconcile one expired ambiguous ingestion job",
    )
    reconcile.add_argument("job_id")
    return parser


def _database_path(database_url: str, *, project_root: Path) -> Path:
    relative = database_url.removeprefix("sqlite:///")
    path = Path(relative)
    return path if path.is_absolute() else project_root / path


def _build_ingestion_runtime() -> IngestionRuntime:
    project_root = Path(__file__).resolve().parents[2]
    settings = Settings()
    if settings.lightrag_api_key is None:
        raise RuntimeError("LIGHTRAG_API_KEY is required for the ingestion worker")
    database = Database(_database_path(settings.database_url, project_root=project_root))
    database.initialize()
    registry = DocumentRegistry.from_processed_manuals(
        manual_dir=project_root / "data" / "manuals",
        processed_dir=project_root / "data" / "processed" / "manuals",
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        namespace="energyops-manuals-v1",
    )
    adapter = LightRAGRestAdapter(
        base_url=str(settings.lightrag_base_url),
        api_key=settings.lightrag_api_key,
        timeout_seconds=settings.lightrag_timeout_seconds,
        max_retries=settings.lightrag_max_retries,
        source_resolver=registry,
    )
    service = IngestionService(
        registry=registry,
        jobs=IngestJobRepository(database),
        rag=adapter,
        worker_id=f"worker-{os.getpid()}",
    )
    return IngestionRuntime(service=service, adapter=adapter)


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: Callable[[], IngestionRuntime] = _build_ingestion_runtime,
) -> int:
    """Parse the stable CLI surface and dispatch through shared services."""

    args = _build_parser().parse_args(argv)
    if args.command is None:
        return 0
    runtime = runtime_factory()
    try:
        if args.command == "ingest-worker":
            result = run_worker(
                runtime.service,
                once=args.once,
                idle_seconds=args.idle_seconds,
            )
            if result is None:
                print("NO_PENDING_JOB")
            else:
                print(f"job_id={result.job_id} status={result.status}")
            return 0
        result = runtime.service.recover_expired(args.job_id)
        print(f"job_id={result.job_id} status={result.status}")
    finally:
        runtime.adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
