from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient
from industrial_rag.api import create_app
from industrial_rag.db.session import init_db, reset_for_testing


def _run(coro):
    return asyncio.run(coro)


def test_vector_backend_request_is_idempotent_for_matching_pending_task() -> None:
    async def scenario() -> None:
        reset_for_testing()
        await init_db(drop_all=True)
        app = create_app()
        app.state.service_api_key = None
        app.state.runtime = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/v1/knowledge-bases", json={"name": "migration"})
            kb_id = created.json()["id"]
            first = await client.post(
                f"/v1/knowledge-bases/{kb_id}/vector-backend",
                json={"target_backend": "qdrant"},
            )
            second = await client.post(
                f"/v1/knowledge-bases/{kb_id}/vector-backend",
                json={"target_backend": "qdrant"},
            )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["task_id"] == first.json()["task_id"]

    _run(scenario())
