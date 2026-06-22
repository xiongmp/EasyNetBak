"""align_backuptemplate_schema

Revision ID: r4s5t6u7v8w9
Revises: q1w2e3r4t5y6
Create Date: 2026-06-12 10:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "r4s5t6u7v8w9"
down_revision: str | Sequence[str] | None = "q1w2e3r4t5y6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_COLUMNS = (
    "execution_mode",
    "definition_json",
    "description",
    "timeout_seconds",
    "validation_status",
    "validation_message",
    "updated_at",
    "template_mode",
    "interactive_steps",
)


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _table_columns("backuptemplate")
    removable_columns = [column for column in LEGACY_COLUMNS if column in columns]
    if not removable_columns:
        return

    with op.batch_alter_table("backuptemplate") as batch_op:
        for column in removable_columns:
            batch_op.drop_column(column)


def downgrade() -> None:
    # The removed columns belong to a legacy schema and cannot be restored safely.
    pass
