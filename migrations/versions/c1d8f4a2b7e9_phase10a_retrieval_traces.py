"""phase10a immutable retrieval traces

Revision ID: c1d8f4a2b7e9
Revises: b9c4e7f2a6d1
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d8f4a2b7e9"
down_revision: str | Sequence[str] | None = "b9c4e7f2a6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_traces",
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("knowledge_base_id", sa.String(32), nullable=False),
        sa.Column("generation_id", sa.String(32), nullable=False),
        sa.Column("trace_version", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_retrieval_traces_trace_id", "retrieval_traces", ["trace_id"]
    )
    op.create_index(
        "ix_retrieval_traces_knowledge_base_id",
        "retrieval_traces",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_retrieval_traces_generation_id",
        "retrieval_traces",
        ["generation_id"],
    )
    op.create_index(
        "ix_retrieval_traces_expires_at", "retrieval_traces", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_traces_expires_at", table_name="retrieval_traces")
    op.drop_index("ix_retrieval_traces_generation_id", table_name="retrieval_traces")
    op.drop_index("ix_retrieval_traces_knowledge_base_id", table_name="retrieval_traces")
    op.drop_index("ix_retrieval_traces_trace_id", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
