"""Integration tests for KB CRUD API (end-to-end).

Uses sync test functions with an async helper — avoids pytest-asyncio
strict-mode quirks with httpx async fixtures.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from industrial_rag.api import create_app
from industrial_rag.db.session import init_db, reset_for_testing


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_db(tmp_path_factory):
    """Isolate this module from the real application database."""
    db_path = tmp_path_factory.mktemp("kb_api_db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    reset_for_testing()
    yield
    os.environ.pop("DATABASE_URL", None)
    reset_for_testing()

# ---------------------------------------------------------------------------
# Helper: run an async test body in a clean event loop
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine to completion, creating a fresh event loop for each call."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _db():
    """Drop and recreate all DB tables before each test."""
    reset_for_testing()

    async def _init():
        await init_db(drop_all=True)

    _run(_init())
    yield
    _run(_init())


def _make_client():
    app = create_app()
    # Ensure lifespan middleware has initialised state keys
    app.state.service_api_key = None
    app.state.runtime = None
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_knowledge_base():
    async def _test():
        async with _make_client() as client:
            resp = await client.post(
                "/v1/knowledge-bases",
                json={"name": "测试知识库", "description": "用于测试的知识库"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["name"] == "测试知识库"
            assert body["status"] == "ready"
            assert body["vector_backend"] == "nano"
            assert body["active_vector_generation"] is None
            assert body["id"]

    _run(_test())


def test_create_empty_name_rejected():
    async def _test():
        async with _make_client() as client:
            resp = await client.post("/v1/knowledge-bases", json={"name": ""})
            assert resp.status_code == 422

    _run(_test())


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_knowledge_bases():
    async def _test():
        async with _make_client() as client:
            await client.post("/v1/knowledge-bases", json={"name": "KB-A"})
            await client.post("/v1/knowledge-bases", json={"name": "KB-B"})
            resp = await client.get("/v1/knowledge-bases")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] >= 2

    _run(_test())


# ---------------------------------------------------------------------------
# Get / Update
# ---------------------------------------------------------------------------


def test_get_knowledge_base():
    async def _test():
        async with _make_client() as client:
            resp1 = await client.post("/v1/knowledge-bases", json={"name": "KB-X"})
            kb_id = resp1.json()["id"]
            resp = await client.get(f"/v1/knowledge-bases/{kb_id}")
            assert resp.status_code == 200
            assert resp.json()["name"] == "KB-X"

    _run(_test())


def test_get_not_found():
    async def _test():
        async with _make_client() as client:
            resp = await client.get("/v1/knowledge-bases/nonexistent123")
            assert resp.status_code == 404

    _run(_test())


def test_update_knowledge_base():
    async def _test():
        async with _make_client() as client:
            resp1 = await client.post("/v1/knowledge-bases", json={"name": "old"})
            kb_id = resp1.json()["id"]
            resp = await client.patch(
                f"/v1/knowledge-bases/{kb_id}",
                json={"name": "renamed", "description": "updated desc"},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "renamed"

    _run(_test())


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_knowledge_base():
    async def _test():
        async with _make_client() as client:
            resp1 = await client.post("/v1/knowledge-bases", json={"name": "to-delete"})
            kb_id = resp1.json()["id"]
            resp = await client.delete(f"/v1/knowledge-bases/{kb_id}")
            assert resp.status_code == 202
            body = resp.json()
            assert body["knowledge_base_id"] == kb_id
            assert "task_id" in body

    _run(_test())


def test_delete_not_found():
    async def _test():
        async with _make_client() as client:
            resp = await client.delete("/v1/knowledge-bases/nonexistent")
            assert resp.status_code == 404

    _run(_test())


def test_legacy_query_still_works():
    async def _test():
        async with _make_client() as client:
            resp = await client.get("/readyz")
            # Without runtime initialised, /readyz returns INDEX_NOT_READY
            assert resp.status_code == 503
            body = resp.json()
            assert body["code"] == "INDEX_NOT_READY"

    _run(_test())
