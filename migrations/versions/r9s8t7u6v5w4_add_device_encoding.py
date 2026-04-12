"""add_device_encoding

Revision ID: r9s8t7u6v5w4
Revises: p4q5r6s7t8u9
Create Date: 2026-04-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r9s8t7u6v5w4"
down_revision: Union[str, Sequence[str], None] = "p4q5r6s7t8u9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("device")}
    if "encoding" not in columns:
        op.add_column("device", sa.Column("encoding", sa.String(), nullable=False, server_default="utf-8"))
    op.execute("UPDATE device SET encoding = 'utf-8' WHERE encoding IS NULL OR TRIM(encoding) = ''")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("device", schema=None) as batch_op:
            batch_op.alter_column("encoding", existing_type=sa.String(), server_default=None)
    else:
        op.alter_column("device", "encoding", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("device")}
    if "encoding" in columns:
        op.drop_column("device", "encoding")
