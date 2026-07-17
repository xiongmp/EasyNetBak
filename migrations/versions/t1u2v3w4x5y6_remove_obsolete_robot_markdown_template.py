"""remove the obsolete duplicate robot markdown template

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-07-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, Sequence[str], None] = "s0t1u2v3w4x5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OBSOLETE_KEY = "builtin_backup_robot_markdown"
_CURRENT_KEY = "builtin_backup_robot"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "notificationtemplate" not in tables:
        raise RuntimeError("Notification template table is missing; apply earlier notification migrations first.")
    obsolete_id = bind.execute(
        sa.text("SELECT id FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": _OBSOLETE_KEY},
    ).scalar_one_or_none()
    if obsolete_id is None:
        return
    current_id = bind.execute(
        sa.text("SELECT id FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": _CURRENT_KEY},
    ).scalar_one_or_none()
    if current_id is None:
        raise RuntimeError("The current shared robot Markdown template is missing; refusing unsafe cleanup.")
    for table in ("notificationpolicy", "notificationdelivery"):
        if table in tables:
            bind.execute(
                sa.text(f"UPDATE {table} SET template_id = :current_id WHERE template_id = :obsolete_id"),
                {"current_id": int(current_id), "obsolete_id": int(obsolete_id)},
            )
    bind.execute(
        sa.text("DELETE FROM notificationtemplate WHERE id = :obsolete_id"),
        {"obsolete_id": int(obsolete_id)},
    )


def downgrade() -> None:
    # The removed row was an accidental duplicate rather than user data. Recreating it
    # would restore the defect and can violate the unique template-name constraint.
    return
