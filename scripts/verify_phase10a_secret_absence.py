"""Verify that staging role credentials are absent from Phase 10A surfaces."""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value
    return values


def _contains_credential(payload: bytes, credentials: Iterable[bytes]) -> bool:
    return any(credential in payload for credential in credentials)


def _http_bytes(url: str, *, bearer: str | None = None) -> bytes:
    headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def verify(
    *,
    env_path: Path,
    runtime_root: Path,
    api_base_url: str,
    ui_url: str,
) -> dict[str, object]:
    env = _load_env(env_path)
    service_key = env.get("SERVICE_API_KEY", "").strip()
    admin_key = env.get("ADMIN_API_KEY", "").strip()
    if not service_key or not admin_key or service_key == admin_key:
        raise ValueError("distinct configured role credentials are required")
    credentials = (service_key.encode(), admin_key.encode())

    baseline_rows = [
        json.loads(line)
        for line in (
            PROJECT_ROOT / "evaluation/phase10/baseline_results.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    request_id = baseline_rows[0]["response"]["request_id"]
    surfaces: dict[str, list[bytes]] = {
        "api_responses": [
            _http_bytes(f"{api_base_url.rstrip('/')}/health"),
            _http_bytes(
                f"{api_base_url.rstrip('/')}/v1/admin/diagnostics/requests/"
                f"{request_id}/retrieval-trace",
                bearer=admin_key,
            ),
        ],
        "ui_response": [_http_bytes(ui_url)],
        "logs": [
            path.read_bytes()
            for path in sorted((runtime_root / "logs").rglob("*"))
            if path.is_file()
        ],
        "jsonl_and_metrics": [
            path.read_bytes()
            for path in sorted((PROJECT_ROOT / "evaluation/phase10").rglob("*"))
            if path.is_file()
        ],
        "database": [
            (runtime_root / "runtime/industrial_rag_phase9b.db").read_bytes()
        ],
        "ui_source": [
            path.read_bytes()
            for path in sorted((PROJECT_ROOT / "src").rglob("*.py"))
        ],
        "report": [
            path.read_bytes()
            for path in [
                PROJECT_ROOT
                / "docs/phase-10a-evaluation-foundation-retrieval-trace-report.md"
            ]
            if path.exists()
        ],
    }
    category_counts = {
        category: sum(
            _contains_credential(payload, credentials) for payload in payloads
        )
        for category, payloads in surfaces.items()
    }
    confirmed_count = sum(category_counts.values())
    return {
        "scan_version": "phase10a-secret-absence-v1",
        "credential_count": 2,
        "credentials_configured": True,
        "category_scanned_item_counts": {
            category: len(payloads) for category, payloads in surfaces.items()
        },
        "category_confirmed_secret_counts": category_counts,
        "confirmed_secret_count": confirmed_count,
        "passed": confirmed_count == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        default=r"D:\industrial_energy_agent_phase9b_staging\runtime\staging.env",
    )
    parser.add_argument(
        "--runtime-root", default=r"D:\industrial_energy_agent_phase9b_staging"
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--ui-url", default="http://127.0.0.1:8512")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "evaluation/phase10/secret_scan.json"),
    )
    args = parser.parse_args()
    result = verify(
        env_path=Path(args.env_file),
        runtime_root=Path(args.runtime_root),
        api_base_url=args.api_base_url,
        ui_url=args.ui_url,
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"confirmed_secret_count={result['confirmed_secret_count']} "
        f"passed={str(result['passed']).lower()}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
