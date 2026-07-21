"""add notification template enabled state

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-07-16 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, Sequence[str], None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_enabled_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    if "notificationtemplate" not in inspector.get_table_names():
        raise RuntimeError("Notification template table is missing; apply notification routing migrations first.")
    return "enabled" in {column["name"] for column in inspector.get_columns("notificationtemplate")}


def _has_enabled_index() -> bool:
    return any(
        tuple(index.get("column_names") or []) == ("enabled",)
        for index in sa.inspect(op.get_bind()).get_indexes("notificationtemplate")
    )


def upgrade() -> None:
    if not _has_enabled_column():
        op.add_column(
            "notificationtemplate",
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if not _has_enabled_index():
        op.create_index("ix_notificationtemplate_enabled", "notificationtemplate", ["enabled"])


def downgrade() -> None:
    if not _has_enabled_column():
        return
    for index in sa.inspect(op.get_bind()).get_indexes("notificationtemplate"):
        if index.get("name") == "ix_notificationtemplate_enabled":
            op.drop_index("ix_notificationtemplate_enabled", table_name="notificationtemplate")
            break
    with op.batch_alter_table("notificationtemplate") as batch_op:
        batch_op.drop_column("enabled")
