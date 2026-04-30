"""add_task_status_columns

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-04-29 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "w3x4y5z6a7b8"
down_revision: str | Sequence[str] | None = "v2w3x4y5z6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_column("backuprecord", "status"):
        with op.batch_alter_table("backuprecord") as batch_op:
            batch_op.add_column(
                sa.Column("status", sa.String(), nullable=False, server_default="planned")
            )
    if not _has_column("backupschedulerun", "status"):
        with op.batch_alter_table("backupschedulerun") as batch_op:
            batch_op.add_column(
                sa.Column("status", sa.String(), nullable=False, server_default="planned")
            )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE backuprecord
            SET status = CASE
                WHEN finished_at IS NULL THEN 'planned'
                WHEN success IS TRUE THEN 'succeeded'
                ELSE 'failed'
            END
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE backupschedulerun
            SET status = CASE
                WHEN finished_at IS NULL THEN 'running'
                WHEN fail_count > 0 AND success_count > 0 THEN 'partial_failed'
                WHEN fail_count > 0 THEN 'failed'
                ELSE 'succeeded'
            END
            """
        )
    )

    backup_indexes = _index_names("backuprecord")
    if "ix_backuprecord_status" not in backup_indexes:
        op.create_index("ix_backuprecord_status", "backuprecord", ["status"], unique=False)

    run_indexes = _index_names("backupschedulerun")
    if "ix_backupschedulerun_status" not in run_indexes:
        op.create_index("ix_backupschedulerun_status", "backupschedulerun", ["status"], unique=False)

    with op.batch_alter_table("backuprecord") as batch_op:
        batch_op.alter_column("status", server_default=None)
    with op.batch_alter_table("backupschedulerun") as batch_op:
        batch_op.alter_column("status", server_default=None)


def downgrade() -> None:
    backup_indexes = _index_names("backuprecord")
    if "ix_backuprecord_status" in backup_indexes:
        op.drop_index("ix_backuprecord_status", table_name="backuprecord")

    run_indexes = _index_names("backupschedulerun")
    if "ix_backupschedulerun_status" in run_indexes:
        op.drop_index("ix_backupschedulerun_status", table_name="backupschedulerun")

    if _has_column("backupschedulerun", "status"):
        with op.batch_alter_table("backupschedulerun") as batch_op:
            batch_op.drop_column("status")
    if _has_column("backuprecord", "status"):
        with op.batch_alter_table("backuprecord") as batch_op:
            batch_op.drop_column("status")
