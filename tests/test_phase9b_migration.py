"""Schema and migration contracts for Phase 9B operational persistence."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from industrial_rag.db.models import Base, UpdateJobStatus
from industrial_rag.db.session import reset_for_testing
from sqlalchemy import create_engine, inspect


def test_phase9b_models_expose_durable_operational_tables_and_fields() -> None:
    assert {"validation_runs", "kb_operation_leases", "gc_plans"} <= set(
        Base.metadata.tables
    )

    job_columns = set(Base.metadata.tables["update_jobs"].columns.keys())
    assert {
        "worker_id",
        "lease_token",
        "fencing_token",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
        "attempt",
        "max_attempts",
        "checkpoint",
    } <= job_columns

    generation_columns = set(
        Base.metadata.tables["vector_index_generations"].columns.keys()
    )
    assert {
        "protect_from_delete",
        "audit_frozen",
        "retention_until",
        "content_epoch",
        "validated_fingerprint",
    } <= generation_columns

    kb_columns = set(Base.metadata.tables["knowledge_bases"].columns.keys())
    assert {"generation_epoch", "last_rollback_target_generation_id"} <= kb_columns


def test_update_job_statuses_support_claim_recovery_and_completion() -> None:
    values = {status.value for status in UpdateJobStatus}
    assert {
        "pending",
        "claimed",
        "running",
        "validating",
        "succeeded",
        "failed",
        "cancelled",
        "recovery_required",
    } <= values


def test_phase9b_alembic_upgrade_downgrade_round_trip(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "phase9b-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    reset_for_testing()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    assert {"validation_runs", "kb_operation_leases", "gc_plans"} <= set(
        inspector.get_table_names()
    )

    command.downgrade(config, "a7f3c9e2b1d4")
    inspector = inspect(create_engine(database_url))
    assert not {"validation_runs", "kb_operation_leases", "gc_plans"} & set(
        inspector.get_table_names()
    )

    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    assert {"validation_runs", "kb_operation_leases", "gc_plans"} <= set(
        inspector.get_table_names()
    )
    reset_for_testing()

