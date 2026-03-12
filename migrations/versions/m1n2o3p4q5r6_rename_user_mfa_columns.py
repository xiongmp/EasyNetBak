"""rename user mfa columns

Revision ID: m1n2o3p4q5r6
Revises: k2l3m4n5o6p7
Create Date: 2026-03-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = "k2l3m4n5o6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" not in existing_tables:
        return
    columns = [c["name"] for c in inspector.get_columns("user")]
    with op.batch_alter_table("user", schema=None) as batch_op:
        if "totp_enabled" in columns and "mfa_enabled" not in columns:
            batch_op.alter_column("totp_enabled", new_column_name="mfa_enabled")
        if "totp_secret_encrypted" in columns and "mfa_secret_encrypted" not in columns:
            batch_op.alter_column("totp_secret_encrypted", new_column_name="mfa_secret_encrypted")
        if "mfa_enabled" not in columns and "totp_enabled" not in columns:
            batch_op.add_column(sa.Column("mfa_enabled", sa.Boolean(), server_default="0"))
        if "mfa_secret_encrypted" not in columns and "totp_secret_encrypted" not in columns:
            batch_op.add_column(sa.Column("mfa_secret_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" not in existing_tables:
        return
    columns = [c["name"] for c in inspector.get_columns("user")]
    with op.batch_alter_table("user", schema=None) as batch_op:
        if "mfa_enabled" in columns and "totp_enabled" not in columns:
            batch_op.alter_column("mfa_enabled", new_column_name="totp_enabled")
        if "mfa_secret_encrypted" in columns and "totp_secret_encrypted" not in columns:
            batch_op.alter_column("mfa_secret_encrypted", new_column_name="totp_secret_encrypted")
        if "totp_enabled" not in columns and "mfa_enabled" not in columns:
            batch_op.add_column(sa.Column("totp_enabled", sa.Boolean(), server_default="0"))
        if "totp_secret_encrypted" not in columns and "mfa_secret_encrypted" not in columns:
            batch_op.add_column(sa.Column("totp_secret_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
