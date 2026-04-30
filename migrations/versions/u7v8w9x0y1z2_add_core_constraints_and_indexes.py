"""add_core_constraints_and_indexes

Revision ID: u7v8w9x0y1z2
Revises: 330a561966fc
Create Date: 2026-04-27 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "u7v8w9x0y1z2"
down_revision: str | Sequence[str] | None = "330a561966fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any((item.get("name") or "") == constraint_name for item in inspector.get_unique_constraints(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any((item.get("name") or "") == index_name for item in inspector.get_indexes(table_name))


def _assert_unique_values(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"""
            SELECT {column_name}, COUNT(*) AS cnt
            FROM {table_name}
            WHERE {column_name} IS NOT NULL
            GROUP BY {column_name}
            HAVING COUNT(*) > 1
            LIMIT 5
            """
        )
    ).fetchall()
    if rows:
        values = ", ".join(str(row[0]) for row in rows)
        raise RuntimeError(f"{table_name}.{column_name} contains duplicate values: {values}")


def upgrade() -> None:
    _assert_unique_values("device", "name")
    _assert_unique_values("device", "host")
    _assert_unique_values("apikey", "prefix")
    _assert_unique_values("apikey", "key_hash")

    need_device_name_uq = not _has_unique_constraint("device", "uq_device_name")
    need_device_host_uq = not _has_unique_constraint("device", "uq_device_host")
    if need_device_name_uq or need_device_host_uq:
        with op.batch_alter_table("device") as batch_op:
            if need_device_name_uq:
                batch_op.create_unique_constraint("uq_device_name", ["name"])
            if need_device_host_uq:
                batch_op.create_unique_constraint("uq_device_host", ["host"])

    need_apikey_hash_uq = not _has_unique_constraint("apikey", "uq_api_key_key_hash")
    need_apikey_prefix_uq = not _has_unique_constraint("apikey", "uq_api_key_prefix")
    if need_apikey_hash_uq or need_apikey_prefix_uq:
        with op.batch_alter_table("apikey") as batch_op:
            if need_apikey_hash_uq:
                batch_op.create_unique_constraint("uq_api_key_key_hash", ["key_hash"])
            if need_apikey_prefix_uq:
                batch_op.create_unique_constraint("uq_api_key_prefix", ["prefix"])

    if not _has_index("backuprecord", "ix_backup_record_device_started_at"):
        op.create_index("ix_backup_record_device_started_at", "backuprecord", ["device_id", "started_at"], unique=False)
    if not _has_index("backuprecord", "ix_backup_record_started_at_success"):
        op.create_index("ix_backup_record_started_at_success", "backuprecord", ["started_at", "success"], unique=False)
    if not _has_index("auditlog", "ix_audit_log_action_created_at"):
        op.create_index("ix_audit_log_action_created_at", "auditlog", ["action", "created_at"], unique=False)
    if not _has_index("auditlog", "ix_audit_log_resource_type_created_at"):
        op.create_index("ix_audit_log_resource_type_created_at", "auditlog", ["resource_type", "created_at"], unique=False)
    if not _has_index("loginlog", "ix_login_log_status_created_at"):
        op.create_index("ix_login_log_status_created_at", "loginlog", ["status", "created_at"], unique=False)
    if not _has_index("backupschedulerun", "ix_backup_schedule_run_schedule_started_at"):
        op.create_index(
            "ix_backup_schedule_run_schedule_started_at",
            "backupschedulerun",
            ["schedule_id", "started_at"],
            unique=False,
        )
    if not _has_index("webshellrecord", "ix_webshell_record_created_at_user_id"):
        op.create_index(
            "ix_webshell_record_created_at_user_id",
            "webshellrecord",
            ["created_at", "user_id"],
            unique=False,
        )
    if not _has_index("webshellrecord", "ix_webshell_record_created_at_device_id"):
        op.create_index(
            "ix_webshell_record_created_at_device_id",
            "webshellrecord",
            ["created_at", "device_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_webshell_record_created_at_device_id", table_name="webshellrecord")
    op.drop_index("ix_webshell_record_created_at_user_id", table_name="webshellrecord")
    op.drop_index("ix_backup_schedule_run_schedule_started_at", table_name="backupschedulerun")
    op.drop_index("ix_login_log_status_created_at", table_name="loginlog")
    op.drop_index("ix_audit_log_resource_type_created_at", table_name="auditlog")
    op.drop_index("ix_audit_log_action_created_at", table_name="auditlog")
    op.drop_index("ix_backup_record_started_at_success", table_name="backuprecord")
    op.drop_index("ix_backup_record_device_started_at", table_name="backuprecord")

    with op.batch_alter_table("apikey") as batch_op:
        batch_op.drop_constraint("uq_api_key_prefix", type_="unique")
        batch_op.drop_constraint("uq_api_key_key_hash", type_="unique")

    with op.batch_alter_table("device") as batch_op:
        batch_op.drop_constraint("uq_device_host", type_="unique")
        batch_op.drop_constraint("uq_device_name", type_="unique")
