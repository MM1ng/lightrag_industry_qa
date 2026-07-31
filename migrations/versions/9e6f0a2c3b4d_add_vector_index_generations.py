"""add normalized vector index generations

Revision ID: 9e6f0a2c3b4d
Revises: 4f2c7d9a8b1e
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e6f0a2c3b4d"
down_revision: str | Sequence[str] | None = "4f2c7d9a8b1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vector_index_generations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=32), nullable=False),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("generation", sa.String(length=80), nullable=False),
        sa.Column("status", sa.Enum("shadow", "active", "retired", "failed", "deleted", name="vectorindexgenerationstatus"), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("collections", sa.JSON(), nullable=True),
        sa.Column("document_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("child_chunks_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_config_hash", sa.String(length=64), nullable=False),
        sa.Column("chunking_config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_task_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["created_by_task_id"], ["lifecycle_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_base_id", "backend", "generation", name="uq_kb_vector_generation"),
    )
    op.create_index("ix_vector_index_generations_knowledge_base_id", "vector_index_generations", ["knowledge_base_id"])
    op.create_index("ix_vector_index_generations_backend", "vector_index_generations", ["backend"])
    op.create_index("ix_vector_index_generations_status", "vector_index_generations", ["status"])
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(sa.Column("active_vector_generation_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_kb_active_vector_generation",
            "vector_index_generations",
            ["active_vector_generation_id"],
            ["id"],
        )
        batch_op.drop_column("qdrant_generations")
        batch_op.drop_column("active_vector_generation")


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(sa.Column("active_vector_generation", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("qdrant_generations", sa.JSON(), nullable=True))
        batch_op.drop_constraint("fk_kb_active_vector_generation", type_="foreignkey")
        batch_op.drop_column("active_vector_generation_id")
    op.drop_index("ix_vector_index_generations_status", table_name="vector_index_generations")
    op.drop_index("ix_vector_index_generations_backend", table_name="vector_index_generations")
    op.drop_index("ix_vector_index_generations_knowledge_base_id", table_name="vector_index_generations")
    op.drop_table("vector_index_generations")
