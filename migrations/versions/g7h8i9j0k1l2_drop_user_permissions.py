"""drop_user_permissions

Revision ID: g7h8i9j0k1l2
Revises: f1a2b3c4d5e6
Create Date: 2026-03-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permissions" in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.drop_column("permissions")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permissions" not in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.add_column(sa.Column("permissions", sa.String(), nullable=True))
