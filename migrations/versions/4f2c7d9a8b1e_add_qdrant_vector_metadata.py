"""add Qdrant vector backend generation metadata

Revision ID: 4f2c7d9a8b1e
Revises: d7e568c55ad8
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f2c7d9a8b1e"
down_revision: str | Sequence[str] | None = "d7e568c55ad8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(
            sa.Column("vector_backend", sa.String(length=20), nullable=False, server_default="nano")
        )
        batch_op.add_column(
            sa.Column("active_vector_generation", sa.String(length=80), nullable=True)
        )
        batch_op.add_column(sa.Column("qdrant_generations", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_column("qdrant_generations")
        batch_op.drop_column("active_vector_generation")
        batch_op.drop_column("vector_backend")
