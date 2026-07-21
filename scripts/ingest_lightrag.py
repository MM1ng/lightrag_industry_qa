"""Register a manual ingestion job through the EnergyOps business API."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import httpx


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_id", help="Registered manual document_id")
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("STREAMLIT_API_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def submit_ingest(
    *,
    document_id: str,
    api_base_url: str,
    timeout: float,
    service_token: str | None = None,
) -> dict[str, object]:
    headers = {"X-Service-Token": service_token} if service_token else {}
    response = httpx.post(
        f"{api_base_url.rstrip('/')}/api/v1/ingest",
        json={"document_ids": [document_id]},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("EnergyOps ingest API returned an invalid response")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = submit_ingest(
        document_id=args.document_id,
        api_base_url=args.api_base_url,
        timeout=args.timeout,
        service_token=os.getenv("SERVICE_TOKEN"),
    )
    print(f"job_id={payload.get('job_id')} status={payload.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
