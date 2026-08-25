"""Fail-closed Qdrant client/server minor-version compatibility check."""

from __future__ import annotations

from importlib.metadata import version

from httpx import AsyncClient


async def check_qdrant_compatibility(
    qdrant_url: str,
    *,
    expected_minor: str = "1.13",
) -> dict[str, str]:
    client_version = version("qdrant-client")
    async with AsyncClient(timeout=10.0) as client:
        response = await client.get(qdrant_url.rstrip("/") + "/")
        response.raise_for_status()
        server_version = str(response.json().get("version") or "")
    client_minor = ".".join(client_version.split(".")[:2])
    server_minor = ".".join(server_version.split(".")[:2])
    if not server_version or client_minor != expected_minor or server_minor != expected_minor:
        raise RuntimeError(
            "Qdrant client/server minor version does not match the configured release gate"
        )
    return {
        "client_version": client_version,
        "server_version": server_version,
        "expected_minor": expected_minor,
        "compatible": "true",
    }
