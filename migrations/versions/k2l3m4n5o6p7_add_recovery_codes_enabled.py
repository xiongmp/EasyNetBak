"""add_recovery_codes_enabled

Revision ID: k2l3m4n5o6p7
Revises: j7k8l9m0n1o2
Create Date: 2026-03-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, Sequence[str], None] = "j7k8l9m0n1o2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "recovery_codes_enabled" not in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.add_column(sa.Column("recovery_codes_enabled", sa.Boolean(), server_default="0", nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "recovery_codes_enabled" in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.drop_column("recovery_codes_enabled")
