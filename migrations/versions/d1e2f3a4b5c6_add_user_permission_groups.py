"""add_user_permission_groups

Revision ID: d1e2f3a4b5c6
Revises: c2a3b4d5e6f7
Create Date: 2026-03-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c2a3b4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permission_group_codes" not in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.add_column(sa.Column("permission_group_codes", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permission_group_codes" in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.drop_column("permission_group_codes")
