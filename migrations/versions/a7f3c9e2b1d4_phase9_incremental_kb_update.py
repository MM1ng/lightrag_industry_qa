"""phase9 incremental knowledge base update

Revision ID: a7f3c9e2b1d4
Revises: 9e6f0a2c3b4d
Create Date: 2026-08-02

Adds document identity fields (logical_name / source_type), extends the
vector generation lifecycle enum, and creates the incremental update job
table.  SQLite stores enum values as VARCHAR, so expanding the allowed
enum values does not require an ALTER for SQLite; PostgreSQL deployments
would need an ALTER TYPE with the expanded values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f3c9e2b1d4"
down_revision: str | Sequence[str] | None = "9e6f0a2c3b4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Document identity fields (backfilled from existing values; nullable to
    # keep the migration safe for rows created before this revision).
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("logical_name", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("source_type", sa.String(length=50), nullable=True))
    op.execute("UPDATE documents SET logical_name = original_file_name WHERE logical_name IS NULL")
    op.execute("UPDATE documents SET source_type = mime_type WHERE source_type IS NULL")

    # Generation status enum expansion is a no-op on SQLite (VARCHAR storage).
    # The ORM enum now also accepts building/validating/ready/archived/rolled_back.

    op.create_table(
        "update_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=32), nullable=False),
        sa.Column("base_generation_id", sa.String(length=32), nullable=True),
        sa.Column("candidate_generation_id", sa.String(length=32), nullable=True),
        sa.Column(
            "operation",
            sa.Enum("add", "replace", "delete", name="updateoperation"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("old_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("new_content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "building",
                "validating",
                "ready",
                "failed",
                "promoted",
                "rolled_back",
                name="updatejobstatus",
            ),
            nullable=False,
        ),
        sa.Column("current_stage", sa.String(length=200), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("sanitized_error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("approved_by", sa.String(length=50), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["base_generation_id"], ["vector_index_generations.id"]),
        sa.ForeignKeyConstraint(["candidate_generation_id"], ["vector_index_generations.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_update_jobs_knowledge_base_id", "update_jobs", ["knowledge_base_id"])
    op.create_index("ix_update_jobs_candidate_generation_id", "update_jobs", ["candidate_generation_id"])
    op.create_index("ix_update_jobs_document_id", "update_jobs", ["document_id"])
    op.create_index("ix_update_jobs_status", "update_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_update_jobs_status", table_name="update_jobs")
    op.drop_index("ix_update_jobs_document_id", table_name="update_jobs")
    op.drop_index("ix_update_jobs_candidate_generation_id", table_name="update_jobs")
    op.drop_index("ix_update_jobs_knowledge_base_id", table_name="update_jobs")
    op.drop_table("update_jobs")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("source_type")
        batch_op.drop_column("logical_name")
