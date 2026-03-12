"""add user mfa fields

Revision ID: d2f4a9b7c1e3
Revises: 850f7ec72c4b
Create Date: 2026-03-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "d2f4a9b7c1e3"
down_revision: Union[str, Sequence[str], None] = "850f7ec72c4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        with op.batch_alter_table("user", schema=None) as batch_op:
            if "mfa_enabled" not in columns:
                batch_op.add_column(sa.Column("mfa_enabled", sa.Boolean(), server_default="0"))
            if "mfa_secret_encrypted" not in columns:
                batch_op.add_column(sa.Column("mfa_secret_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        with op.batch_alter_table("user", schema=None) as batch_op:
            if "mfa_secret_encrypted" in columns:
                batch_op.drop_column("mfa_secret_encrypted")
            if "mfa_enabled" in columns:
                batch_op.drop_column("mfa_enabled")
