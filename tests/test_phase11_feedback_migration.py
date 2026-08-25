from __future__ import annotations

from alembic import command
from alembic.config import Config
from industrial_rag.db.models import Base
from industrial_rag.db.session import reset_for_testing
from sqlalchemy import create_engine, inspect

EXPECTED_COLUMNS = {
    "id",
    "request_id",
    "trace_id",
    "generation_id",
    "knowledge_base_id",
    "question",
    "answer",
    "answer_status",
    "feedback_type",
    "feedback_reason",
    "feedback_comment",
    "citations",
    "retrieved_chunks",
    "created_at",
    "updated_at",
    "answer_correct",
    "answer_complete",
    "citation_supported",
    "refusal_appropriate",
    "root_cause",
    "review_notes",
}


def test_phase11_model_declares_answer_feedback_table() -> None:
    table = Base.metadata.tables["answer_feedback"]

    assert set(table.columns.keys()) == EXPECTED_COLUMNS
    assert table.primary_key.columns.keys() == ["id"]
    assert any(
        constraint.name == "uq_answer_feedback_request_id"
        for constraint in table.constraints
    )


def test_phase11_alembic_upgrade_downgrade_round_trip(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "phase11-feedback.db"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    sync_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", async_url)
    reset_for_testing()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    inspector = inspect(create_engine(sync_url))
    assert "answer_feedback" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("answer_feedback")}
    assert {
        "ix_answer_feedback_request_id",
        "ix_answer_feedback_trace_id",
        "ix_answer_feedback_generation_id",
        "ix_answer_feedback_knowledge_base_id",
        "ix_answer_feedback_answer_status",
        "ix_answer_feedback_created_at",
    } <= indexes

    command.downgrade(config, "c1d8f4a2b7e9")
    inspector = inspect(create_engine(sync_url))
    assert "answer_feedback" not in inspector.get_table_names()

    command.upgrade(config, "head")
    inspector = inspect(create_engine(sync_url))
    assert "answer_feedback" in inspector.get_table_names()
    reset_for_testing()
