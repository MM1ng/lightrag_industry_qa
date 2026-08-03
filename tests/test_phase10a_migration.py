from __future__ import annotations

from alembic import command
from alembic.config import Config
from industrial_rag.db.models import Base
from industrial_rag.db.session import reset_for_testing
from sqlalchemy import create_engine, inspect


def test_phase10a_model_declares_immutable_retrieval_trace_table() -> None:
    """Catches model drift that would omit required trace lookup and TTL columns."""
    table = Base.metadata.tables["retrieval_traces"]
    assert set(table.columns.keys()) == {
        "request_id",
        "trace_id",
        "knowledge_base_id",
        "generation_id",
        "trace_version",
        "payload",
        "created_at",
        "expires_at",
    }
    assert table.primary_key.columns.keys() == ["request_id"]


def test_phase10a_alembic_upgrade_downgrade_round_trip(tmp_path, monkeypatch) -> None:
    """Catches a migration that cannot create and cleanly remove only the new table."""
    database_path = tmp_path / "phase10a-migration.db"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    sync_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", async_url)
    reset_for_testing()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    inspector = inspect(create_engine(sync_url))
    assert "retrieval_traces" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("retrieval_traces")}
    assert {
        "ix_retrieval_traces_trace_id",
        "ix_retrieval_traces_knowledge_base_id",
        "ix_retrieval_traces_generation_id",
        "ix_retrieval_traces_expires_at",
    } <= indexes

    command.downgrade(config, "b9c4e7f2a6d1")
    inspector = inspect(create_engine(sync_url))
    assert "retrieval_traces" not in inspector.get_table_names()

    command.upgrade(config, "head")
    inspector = inspect(create_engine(sync_url))
    assert "retrieval_traces" in inspector.get_table_names()
    reset_for_testing()
