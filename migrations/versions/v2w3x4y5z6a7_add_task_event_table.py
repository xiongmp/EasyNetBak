"""add_task_event_table

Revision ID: v2w3x4y5z6a7
Revises: u7v8w9x0y1z2
Create Date: 2026-04-27 13:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "v2w3x4y5z6a7"
down_revision: str | Sequence[str] | None = "u7v8w9x0y1z2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns, unique=False)


def upgrade() -> None:
    if not _has_table("taskevent"):
        op.create_table(
            "taskevent",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("record_id", sa.String(), nullable=True),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=True),
            sa.Column("device_id", sa.Integer(), nullable=True),
            sa.Column("failure_type", sa.String(), nullable=True),
            sa.Column("storage_type", sa.String(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True),
            sa.Column("retries_done", sa.Integer(), nullable=True),
            sa.Column("max_retries", sa.Integer(), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_taskevent_created_at", "taskevent", ["created_at"])
    _create_index_if_missing("ix_taskevent_device_id", "taskevent", ["device_id"])
    _create_index_if_missing("ix_taskevent_event", "taskevent", ["event"])
    _create_index_if_missing("ix_taskevent_failure_type", "taskevent", ["failure_type"])
    _create_index_if_missing("ix_taskevent_record_id", "taskevent", ["record_id"])
    _create_index_if_missing("ix_taskevent_request_id", "taskevent", ["request_id"])
    _create_index_if_missing("ix_taskevent_run_id", "taskevent", ["run_id"])
    _create_index_if_missing("ix_taskevent_storage_type", "taskevent", ["storage_type"])
    _create_index_if_missing("ix_taskevent_success", "taskevent", ["success"])
    _create_index_if_missing("ix_taskevent_task_id", "taskevent", ["task_id"])
    _create_index_if_missing("ix_task_event_event_created_at", "taskevent", ["event", "created_at"])
    _create_index_if_missing("ix_task_event_record_event_created_at", "taskevent", ["record_id", "event", "created_at"])
    _create_index_if_missing("ix_task_event_run_event_created_at", "taskevent", ["run_id", "event", "created_at"])


def downgrade() -> None:
    if not _has_table("taskevent"):
        return
    existing_indexes = _index_names("taskevent")
    for index_name in [
        "ix_task_event_run_event_created_at",
        "ix_task_event_record_event_created_at",
        "ix_task_event_event_created_at",
        "ix_taskevent_task_id",
        "ix_taskevent_success",
        "ix_taskevent_storage_type",
        "ix_taskevent_run_id",
        "ix_taskevent_request_id",
        "ix_taskevent_record_id",
        "ix_taskevent_failure_type",
        "ix_taskevent_event",
        "ix_taskevent_device_id",
        "ix_taskevent_created_at",
    ]:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="taskevent")
    op.drop_table("taskevent")
