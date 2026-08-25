"""phase11 answer feedback snapshots

Revision ID: e2f3a4b5c6d7
Revises: c1d8f4a2b7e9
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "c1d8f4a2b7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("generation_id", sa.String(32), nullable=True),
        sa.Column("knowledge_base_id", sa.String(32), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("answer_status", sa.String(32), nullable=False),
        sa.Column("feedback_type", sa.String(20), nullable=True),
        sa.Column("feedback_reason", sa.String(64), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("retrieved_chunks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answer_correct", sa.String(20), nullable=True),
        sa.Column("answer_complete", sa.String(20), nullable=True),
        sa.Column("citation_supported", sa.String(20), nullable=True),
        sa.Column("refusal_appropriate", sa.String(20), nullable=True),
        sa.Column("root_cause", sa.String(64), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_answer_feedback_request_id"),
    )
    op.create_index("ix_answer_feedback_request_id", "answer_feedback", ["request_id"])
    op.create_index("ix_answer_feedback_trace_id", "answer_feedback", ["trace_id"])
    op.create_index(
        "ix_answer_feedback_generation_id", "answer_feedback", ["generation_id"]
    )
    op.create_index(
        "ix_answer_feedback_knowledge_base_id",
        "answer_feedback",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_answer_feedback_answer_status", "answer_feedback", ["answer_status"]
    )
    op.create_index("ix_answer_feedback_created_at", "answer_feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_created_at", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_answer_status", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_knowledge_base_id", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_generation_id", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_trace_id", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_request_id", table_name="answer_feedback")
    op.drop_table("answer_feedback")
