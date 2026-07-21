from __future__ import annotations

import os

import httpx
import pytest

from industrial_energy_agent.config.settings import Settings
from industrial_energy_agent.rag.lightrag_adapter import LightRAGRestAdapter

pytestmark = [pytest.mark.external, pytest.mark.smoke]


@pytest.fixture(scope="module")
def live_adapter() -> LightRAGRestAdapter:
    if os.environ.get("RUN_LIGHTRAG_SMOKE") != "1":
        pytest.skip("set RUN_LIGHTRAG_SMOKE=1 to run the live LightRAG contract smoke")
    settings = Settings(_env_file=None)
    if settings.lightrag_api_key is None:
        pytest.skip("LIGHTRAG_API_KEY is not set in the current process")
    adapter = LightRAGRestAdapter(
        base_url=str(settings.lightrag_base_url).rstrip("/"),
        api_key=settings.lightrag_api_key,
        timeout_seconds=settings.lightrag_timeout_seconds,
        max_retries=settings.lightrag_max_retries,
    )
    yield adapter
    adapter.close()


def test_locked_server_health(live_adapter: LightRAGRestAdapter) -> None:
    health = live_adapter.health_check()

    assert health.healthy
    assert health.core_version == "1.5.4"
    assert health.configuration.llm_model == "qwen3.7-plus"
    assert health.configuration.embedding_model == "text-embedding-v4"


def test_api_key_gate_rejects_missing_header() -> None:
    if os.environ.get("RUN_LIGHTRAG_SMOKE") != "1":
        pytest.skip("set RUN_LIGHTRAG_SMOKE=1 to run the live LightRAG contract smoke")
    settings = Settings(_env_file=None)

    response = httpx.post(
        f"{str(settings.lightrag_base_url).rstrip('/')}/documents/paginated",
        json={"page": 1, "page_size": 10},
        timeout=settings.lightrag_timeout_seconds,
    )

    assert response.status_code == 403


@pytest.mark.parametrize("mode", ["local", "global", "hybrid", "naive", "mix"])
def test_locked_query_modes(mode: str, live_adapter: LightRAGRestAdapter) -> None:
    result = live_adapter.search(
        "离心泵轴承异常时应检查什么?",
        mode=mode,  # type: ignore[arg-type]
        top_k=5,
    )

    assert result.mode == mode
