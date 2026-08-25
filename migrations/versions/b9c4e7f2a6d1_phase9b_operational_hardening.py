"""phase9b operational hardening persistence

Revision ID: b9c4e7f2a6d1
Revises: a7f3c9e2b1d4
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9c4e7f2a6d1"
down_revision: str | Sequence[str] | None = "a7f3c9e2b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(
            sa.Column("generation_epoch", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("last_rollback_target_generation_id", sa.String(32), nullable=True)
        )
        batch_op.create_index(
            "ix_knowledge_bases_last_rollback_target_generation_id",
            ["last_rollback_target_generation_id"],
        )
        batch_op.create_foreign_key(
            "fk_kb_last_rollback_generation",
            "vector_index_generations",
            ["last_rollback_target_generation_id"],
            ["id"],
        )

    with op.batch_alter_table("vector_index_generations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "protect_from_delete", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(
            sa.Column("audit_frozen", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("content_epoch", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("validated_fingerprint", sa.String(64), nullable=True))

    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.add_column(sa.Column("worker_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("lease_token", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("fencing_token", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.add_column(sa.Column("checkpoint", sa.JSON(), nullable=True))
        batch_op.create_index("ix_update_jobs_worker_id", ["worker_id"])
        batch_op.create_index("ix_update_jobs_lease_expires_at", ["lease_expires_at"])

    op.create_table(
        "validation_runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("knowledge_base_id", sa.String(32), nullable=False),
        sa.Column("generation_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("golden_set_version", sa.String(100), nullable=False),
        sa.Column("golden_set_sha256", sa.String(64), nullable=False),
        sa.Column("runner_version", sa.String(100), nullable=False),
        sa.Column("app_git_commit", sa.String(40), nullable=False),
        sa.Column("configured_model", sa.String(100), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(64), nullable=False),
        sa.Column("generation_manifest_hash", sa.String(64), nullable=False),
        sa.Column("qdrant_content_fingerprint", sa.String(64), nullable=False),
        sa.Column("document_registry_fingerprint", sa.String(64), nullable=False),
        sa.Column("generation_content_epoch", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_artifact_path", sa.Text(), nullable=True),
        sa.Column("result_artifact_sha256", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["generation_id"], ["vector_index_generations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_runs_knowledge_base_id", "validation_runs", ["knowledge_base_id"])
    op.create_index("ix_validation_runs_generation_id", "validation_runs", ["generation_id"])
    op.create_index("ix_validation_runs_status", "validation_runs", ["status"])
    op.create_index("ix_validation_runs_expires_at", "validation_runs", ["expires_at"])

    op.create_table(
        "kb_operation_leases",
        sa.Column("knowledge_base_id", sa.String(32), nullable=False),
        sa.Column("lock_owner", sa.String(100), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation", sa.String(50), nullable=True),
        sa.Column("job_id", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["update_jobs.id"]),
        sa.PrimaryKeyConstraint("knowledge_base_id"),
    )
    op.create_index("ix_kb_operation_leases_expires_at", "kb_operation_leases", ["expires_at"])
    op.create_index("ix_kb_operation_leases_job_id", "kb_operation_leases", ["job_id"])

    op.create_table(
        "gc_plans",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("knowledge_base_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("approved_by", sa.String(50), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gc_plans_knowledge_base_id", "gc_plans", ["knowledge_base_id"])
    op.create_index("ix_gc_plans_status", "gc_plans", ["status"])
    op.create_index("ix_gc_plans_expires_at", "gc_plans", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_gc_plans_expires_at", table_name="gc_plans")
    op.drop_index("ix_gc_plans_status", table_name="gc_plans")
    op.drop_index("ix_gc_plans_knowledge_base_id", table_name="gc_plans")
    op.drop_table("gc_plans")

    op.drop_index("ix_kb_operation_leases_job_id", table_name="kb_operation_leases")
    op.drop_index("ix_kb_operation_leases_expires_at", table_name="kb_operation_leases")
    op.drop_table("kb_operation_leases")

    op.drop_index("ix_validation_runs_expires_at", table_name="validation_runs")
    op.drop_index("ix_validation_runs_status", table_name="validation_runs")
    op.drop_index("ix_validation_runs_generation_id", table_name="validation_runs")
    op.drop_index("ix_validation_runs_knowledge_base_id", table_name="validation_runs")
    op.drop_table("validation_runs")

    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.drop_index("ix_update_jobs_lease_expires_at")
        batch_op.drop_index("ix_update_jobs_worker_id")
        for column in (
            "checkpoint",
            "max_attempts",
            "attempt",
            "lease_expires_at",
            "heartbeat_at",
            "claimed_at",
            "fencing_token",
            "lease_token",
            "worker_id",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("vector_index_generations") as batch_op:
        for column in (
            "validated_fingerprint",
            "content_epoch",
            "retention_until",
            "audit_frozen",
            "protect_from_delete",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_constraint("fk_kb_last_rollback_generation", type_="foreignkey")
        batch_op.drop_index("ix_knowledge_bases_last_rollback_target_generation_id")
        batch_op.drop_column("last_rollback_target_generation_id")
        batch_op.drop_column("generation_epoch")

