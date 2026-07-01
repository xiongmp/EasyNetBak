"""backfill enable_watermark column

Revision ID: b7c8d9e0f1a2
Revises: 495966c382b0
Create Date: 2026-07-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "495966c382b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "enable_watermark" not in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "enable_watermark",
                        sa.Boolean(),
                        server_default=sa.text("true"),
                        nullable=False,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "enable_watermark" in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.drop_column("enable_watermark")
