from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from industrial_energy_agent.persistence.database import Database

EXPECTED_TABLES = {
    "schema_migrations",
    "conversation_sessions",
    "request_summaries",
    "trace_events",
    "diagnoses",
    "work_orders",
    "risk_reviews",
    "work_order_reviews",
    "ingest_jobs",
}


def test_database_initialization_is_idempotent_and_applies_exact_schema(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "energyops.sqlite", busy_timeout_ms=2_500)

    database.initialize()
    database.initialize()

    with database.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            if not row[0].startswith("sqlite_")
        }
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 2_500

    assert tables == EXPECTED_TABLES
    assert [row[0] for row in migrations] == ["0001_initial.sql"]


def test_explicit_transaction_rolls_back_the_whole_unit_of_work(tmp_path: Path) -> None:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()

    with (
        pytest.raises(RuntimeError, match="abort"),
        database.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO conversation_sessions (
                conversation_id, selected_cycle_id, summary_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("conv-rollback", None, "{}", "2026-07-21T00:00:00Z", "2026-07-21T00:00:00Z"),
        )
        raise RuntimeError("abort")

    with database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM conversation_sessions WHERE conversation_id = ?",
            ("conv-rollback",),
        ).fetchone()[0]
    assert count == 0


def test_foreign_keys_and_non_executing_work_order_checks_are_enforced(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()

    with database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO work_order_reviews (
                    review_id, work_order_id, request_id, idempotency_key,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "review-orphan",
                    "missing-work-order",
                    "request-1",
                    "idem-1",
                    "PENDING_REVIEW",
                    "2026-07-21T00:00:00Z",
                ),
            )
        connection.execute(
            """
            INSERT INTO conversation_sessions (
                conversation_id, selected_cycle_id, summary_json, created_at, updated_at
            ) VALUES (?, NULL, '{}', ?, ?)
            """,
            ("conv-1", "2026-07-21T00:00:00Z", "2026-07-21T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO diagnoses (
                diagnosis_id, request_id, conversation_id, payload_json, created_at
            ) VALUES (?, ?, ?, '{}', ?)
            """,
            ("diag-1", "request-1", "conv-1", "2026-07-21T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO work_orders (
                    work_order_id, request_id, conversation_id, diagnosis_id,
                    payload_json, status, approval_status, executed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "wo-invalid",
                    "request-1",
                    "conv-1",
                    "diag-1",
                    "{}",
                    "DRAFT",
                    "PENDING_REVIEW",
                    1,
                    "2026-07-21T00:00:00Z",
                ),
            )


def test_risk_review_hash_check_rejects_non_lowercase_hex_suffix(tmp_path: Path) -> None:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()

    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO conversation_sessions (
                conversation_id, selected_cycle_id, summary_json, created_at, updated_at
            ) VALUES (?, NULL, '{}', ?, ?)
            """,
            ("conv-1", "2026-07-21T00:00:00Z", "2026-07-21T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO risk_reviews (
                    review_id, request_id, conversation_id, risk_category,
                    restricted_answer_hash, idempotency_key, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?)
                """,
                (
                    "risk-invalid-hash",
                    "request-1",
                    "conv-1",
                    "operation_command",
                    "sha256:" + "a" * 63 + "g",
                    "risk-idem-1",
                    "2026-07-21T00:00:00Z",
                ),
            )
