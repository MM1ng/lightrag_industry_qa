"""Run the default Ragas Conversation Retrieval Development migration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

MIGRATION_REPORT_PATH = PROJECT_ROOT / "evaluation/phase10/ragas_migration_development_report.json"

def _load_staging_environment() -> None:
    path = PROJECT_ROOT / ".env.local_staging"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


async def run(output: Path = MIGRATION_REPORT_PATH) -> int:
    from evaluation.ragas.conversation_adapter import build_ragas_dataset
    from evaluation.ragas.migration_runner import (
        build_blocked_from_exception,
        run_ragas_experiment,
        write_report,
    )
    from industrial_rag.config import Settings
    from industrial_rag.lightrag_service import LightRAGService, QueryOptions
    from industrial_rag.vector_collections import VectorBackend

    bundle = build_ragas_dataset()
    baseline_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    _load_staging_environment()
    required = ("QDRANT_URL", "QDRANT_KB_ID", "QDRANT_GENERATION", "LIGHTRAG_WORKING_DIR", "DASHSCOPE_API_KEY")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        report = build_blocked_from_exception(RuntimeError(f"missing staging configuration: {', '.join(missing)}"), bundle)
        write_report(report, output)
        return 2
    settings = Settings.from_env()
    if settings.vector_backend is not VectorBackend.qdrant:
        report = build_blocked_from_exception(RuntimeError("Ragas migration requires VECTOR_BACKEND=qdrant"), bundle)
        write_report(report, output)
        return 2
    service = LightRAGService(settings)
    try:
        await service.initialize()
        report = await run_ragas_experiment(
            service._backend,
            cases=bundle.cases,
            config=QueryOptions(
                mode=settings.phase10b_query_mode,
                top_k=settings.phase10b_top_k,
                chunk_top_k=settings.phase10b_chunk_top_k,
                enable_rerank=False,
            ),
            fingerprint={
                "knowledge_base_id": settings.qdrant_kb_id,
                "generation_id": settings.qdrant_generation,
                "workspace": str(settings.working_dir),
                "vector_backend": settings.vector_backend.value,
                "embedding_model": settings.embedding_model,
            },
            baseline_head=baseline_head,
        )
    except Exception as error:
        report = build_blocked_from_exception(error, bundle)
    finally:
        await service.close()
    write_report(report, output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "MIGRATION_PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=MIGRATION_REPORT_PATH)
    args = parser.parse_args()
    return asyncio.run(run(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
