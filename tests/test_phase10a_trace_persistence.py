from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from industrial_rag.config import Settings
from industrial_rag.db.models import Base
from industrial_rag.db.session import get_session_factory, get_trace_session_factory
from industrial_rag.repositories.retrieval_trace_repository import (
    RetrievalTraceRepository,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
PAYLOAD = {"trace_version": "phase10a-retrieval-trace-v1", "initial_results": []}


@pytest.mark.asyncio
async def test_trace_repository_is_insert_only_and_hides_expired_records(tmp_path) -> None:
    """Catches replacement of immutable traces or serving a record beyond its TTL."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session, session.begin():
        await RetrievalTraceRepository(session).create_immutable(
            request_id="request-1",
            trace_id="trace-1",
            knowledge_base_id="kb-1",
            generation_id="generation-1",
            trace_version="phase10a-retrieval-trace-v1",
            payload=PAYLOAD,
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
        )

    async with factory() as session:
        repository = RetrievalTraceRepository(session)
        record = await repository.get_unexpired("request-1", now=NOW)
        assert record is not None
        assert record.payload == PAYLOAD
        assert await repository.get_unexpired(
            "request-1", now=NOW + timedelta(seconds=61)
        ) is None

    async with factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await RetrievalTraceRepository(session).create_immutable(
                    request_id="request-1",
                    trace_id="trace-replacement",
                    knowledge_base_id="kb-1",
                    generation_id="generation-1",
                    trace_version="phase10a-retrieval-trace-v1",
                    payload={"replacement": True},
                    created_at=NOW,
                    expires_at=NOW + timedelta(seconds=60),
                )

    async with factory() as session:
        record = await RetrievalTraceRepository(session).get_unexpired(
            "request-1", now=NOW
        )
        assert record is not None
        assert record.trace_id == "trace-1"
        assert record.payload == PAYLOAD
    await engine.dispose()


def test_trace_ttl_defaults_and_rejects_values_outside_frozen_range() -> None:
    """Catches silently unbounded or too-short trace retention configuration."""
    base = {"DASHSCOPE_API_KEY": "test-only-key"}
    assert Settings.from_mapping(base).retrieval_trace_ttl_seconds == 86_400
    for invalid in ("59", "604801", "not-an-integer"):
        with pytest.raises(ValueError, match="RETRIEVAL_TRACE_TTL_SECONDS"):
            Settings.from_mapping({**base, "RETRIEVAL_TRACE_TTL_SECONDS": invalid})


def test_trace_session_factory_is_distinct_from_request_session_factory() -> None:
    """Catches accidental reuse of the request transaction for trace persistence."""
    assert get_trace_session_factory() is not get_session_factory()

