"""Probe the locked LightRAG REST contract without loading a local .env file."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from uuid import uuid4

import httpx

from industrial_energy_agent.config.settings import Settings
from industrial_energy_agent.rag.base import RAGDocument, VerifiedSearchMode
from industrial_energy_agent.rag.lightrag_adapter import LightRAGRestAdapter

VERIFIED_MODES: tuple[VerifiedSearchMode, ...] = (
    "local",
    "global",
    "hybrid",
    "naive",
    "mix",
)


def require_api_key_gate(client: httpx.Client) -> None:
    """Require a protected document route to reject a request without X-API-Key."""

    response = client.post("/documents/paginated", json={"page": 1, "page_size": 10})
    if response.status_code != 403:
        raise RuntimeError("LightRAG did not enforce the expected API-key gate")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-all-modes",
        action="store_true",
        help="Query local/global/hybrid/naive/mix and fail if any mode is rejected.",
    )
    parser.add_argument(
        "--exercise-insert",
        action="store_true",
        help="Insert one text and one two-text batch before querying (uses model quota).",
    )
    parser.add_argument(
        "--expected-working-directory",
        help="Require the health response to use this exact isolated working directory.",
    )
    parser.add_argument("--query", default="离心泵轴承异常时应检查什么?")
    return parser.parse_args(argv)


def _wait_for_track(adapter: LightRAGRestAdapter, track_id: str) -> None:
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        status = adapter.track_status(track_id)
        states = {document.status.removeprefix("DocStatus.") for document in status.documents}
        if states and states <= {"PROCESSED", "FAILED"}:
            if "FAILED" in states:
                raise RuntimeError("LightRAG insert track failed")
            return
        time.sleep(2)
    raise TimeoutError("LightRAG insert track did not finish within 360 seconds")


def _exercise_insert(adapter: LightRAGRestAdapter) -> None:
    marker = uuid4().hex
    single = adapter.ingest_documents(
        [
            RAGDocument(
                text=(
                    f"EnergyOps contract marker {marker}. Pump bearing inspection checks "
                    "lubrication, temperature, and abnormal vibration."
                ),
                file_source=f"task8-probe-single-{marker}.txt",
            )
        ]
    )
    _wait_for_track(adapter, single.track_id)
    print("PASS insert route=/documents/text")

    batch = adapter.ingest_documents(
        [
            RAGDocument(
                text=f"EnergyOps contract marker {marker}-a. Follow approved isolation procedures.",
                file_source=f"task8-probe-batch-a-{marker}.txt",
            ),
            RAGDocument(
                text=f"EnergyOps contract marker {marker}-b. Require qualified human confirmation.",
                file_source=f"task8-probe-batch-b-{marker}.txt",
            ),
        ]
    )
    _wait_for_track(adapter, batch.track_id)
    print("PASS insert route=/documents/texts")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings(_env_file=None)
    if settings.lightrag_api_key is None:
        raise RuntimeError("LIGHTRAG_API_KEY must be set in the current process")
    base_url = str(settings.lightrag_base_url).rstrip("/")
    with httpx.Client(
        base_url=base_url,
        timeout=settings.lightrag_timeout_seconds,
    ) as unauthenticated_client:
        require_api_key_gate(unauthenticated_client)
    with LightRAGRestAdapter(
        base_url=base_url,
        api_key=settings.lightrag_api_key,
        timeout_seconds=settings.lightrag_timeout_seconds,
        max_retries=settings.lightrag_max_retries,
    ) as adapter:
        health = adapter.health_check()
        configuration = health.configuration
        if not health.healthy or health.core_version != "1.5.4":
            raise RuntimeError("Unexpected LightRAG health or core version")
        if configuration.llm_model != "qwen3.7-plus":
            raise RuntimeError("LightRAG chat model is not locked to qwen3.7-plus")
        if configuration.embedding_model != "text-embedding-v4":
            raise RuntimeError("LightRAG embedding model is not locked to text-embedding-v4")
        if (
            args.expected_working_directory is not None
            and health.working_directory != args.expected_working_directory
        ):
            raise RuntimeError("LightRAG is using an unexpected working directory")
        print(f"PASS health core={health.core_version} api_key_gate=403")

        if args.exercise_insert:
            _exercise_insert(adapter)
        if args.require_all_modes:
            for mode in VERIFIED_MODES:
                result = adapter.search(args.query, mode=mode, top_k=5)
                print(
                    f"PASS query mode={mode} entities={len(result.entities)} "
                    f"relationships={len(result.relationships)} chunks={len(result.chunks)} "
                    f"references={len(result.references)}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
