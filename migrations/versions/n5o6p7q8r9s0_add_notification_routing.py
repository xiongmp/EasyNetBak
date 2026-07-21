"""add notification templates, routing and deliveries

Revision ID: n5o6p7q8r9s0
Revises: c8d9e0f1a2b3
Create Date: 2026-07-16 00:00:00.000000
"""
from typing import Sequence, Union
from datetime import datetime
import json

from alembic import op
import sqlalchemy as sa


revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _validate_existing_table(name: str, required_columns: set[str]) -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(name)}
    missing = sorted(required_columns - columns)
    if missing:
        raise RuntimeError(
            f"Existing table {name!r} is incompatible with notification routing; "
            f"missing columns: {', '.join(missing)}"
        )


def _ensure_index(name: str, table: str, columns: list[str]) -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def _repair_notification_delivery_payload() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("notificationdelivery")
    }
    if "payload_json" not in columns:
        op.add_column(
            "notificationdelivery",
            sa.Column(
                "payload_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def upgrade() -> None:
    if not _has_table("notificationchannel"):
        op.create_table(
        "notificationchannel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("builtin_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("builtin_key", name="uq_notification_channel_builtin_key"),
        sa.UniqueConstraint("name", name="uq_notification_channel_name"),
        )
    else:
        _validate_existing_table("notificationchannel", {
            "id", "name", "channel_type", "enabled", "config_json", "secret_encrypted",
            "builtin_key", "created_at", "updated_at",
        })
    _ensure_index("ix_notification_channel_type_enabled", "notificationchannel", ["channel_type", "enabled"])
    _ensure_index("ix_notificationchannel_channel_type", "notificationchannel", ["channel_type"])
    _ensure_index("ix_notificationchannel_created_at", "notificationchannel", ["created_at"])
    _ensure_index("ix_notificationchannel_enabled", "notificationchannel", ["enabled"])
    _ensure_index("ix_notificationchannel_updated_at", "notificationchannel", ["updated_at"])

    if not _has_table("notificationtemplate"):
        op.create_table(
        "notificationtemplate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("subject_template", sa.Text(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_notification_template_name"),
        )
    else:
        _validate_existing_table("notificationtemplate", {
            "id", "name", "event_type", "channel_type", "locale", "subject_template",
            "body_template", "content_type", "created_at", "updated_at",
        })
    _ensure_index("ix_notification_template_event_channel", "notificationtemplate", ["event_type", "channel_type"])
    _ensure_index("ix_notificationtemplate_channel_type", "notificationtemplate", ["channel_type"])
    _ensure_index("ix_notificationtemplate_created_at", "notificationtemplate", ["created_at"])
    _ensure_index("ix_notificationtemplate_event_type", "notificationtemplate", ["event_type"])
    _ensure_index("ix_notificationtemplate_locale", "notificationtemplate", ["locale"])
    _ensure_index("ix_notificationtemplate_updated_at", "notificationtemplate", ["updated_at"])

    if not _has_table("notificationpolicy"):
        op.create_table(
        "notificationpolicy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("event_types_json", sa.Text(), nullable=False),
        sa.Column("group_ids_json", sa.Text(), nullable=False),
        sa.Column("include_descendants", sa.Boolean(), nullable=False),
        sa.Column("platforms_json", sa.Text(), nullable=False),
        sa.Column("failure_types_json", sa.Text(), nullable=False),
        sa.Column("channel_ids_json", sa.Text(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("stop_processing", sa.Boolean(), nullable=False),
        sa.Column("builtin_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["notificationtemplate.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("builtin_key", name="uq_notification_policy_builtin_key"),
        sa.UniqueConstraint("name", name="uq_notification_policy_name"),
        )
    else:
        _validate_existing_table("notificationpolicy", {
            "id", "name", "enabled", "priority", "event_types_json", "group_ids_json",
            "include_descendants", "platforms_json", "failure_types_json", "channel_ids_json",
            "template_id", "stop_processing", "builtin_key", "created_at", "updated_at",
        })
    _ensure_index("ix_notification_policy_enabled_priority", "notificationpolicy", ["enabled", "priority"])
    _ensure_index("ix_notificationpolicy_created_at", "notificationpolicy", ["created_at"])
    _ensure_index("ix_notificationpolicy_enabled", "notificationpolicy", ["enabled"])
    _ensure_index("ix_notificationpolicy_priority", "notificationpolicy", ["priority"])
    _ensure_index("ix_notificationpolicy_template_id", "notificationpolicy", ["template_id"])
    _ensure_index("ix_notificationpolicy_updated_at", "notificationpolicy", ["updated_at"])

    if not _has_table("notificationevent"):
        op.create_table(
        "notificationevent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key", name="uq_notification_event_source_key"),
        )
    else:
        _validate_existing_table("notificationevent", {
            "id", "event_type", "source_key", "locale", "payload_json", "created_at",
        })
    _ensure_index("ix_notification_event_type_created_at", "notificationevent", ["event_type", "created_at"])
    _ensure_index("ix_notificationevent_created_at", "notificationevent", ["created_at"])
    _ensure_index("ix_notificationevent_event_type", "notificationevent", ["event_type"])
    _ensure_index("ix_notificationevent_id", "notificationevent", ["id"])

    if not _has_table("notificationdelivery"):
        op.create_table(
        "notificationdelivery",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["notificationchannel.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["notificationevent.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["notificationpolicy.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["notificationtemplate.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_delivery_dedupe_key"),
        )
    else:
        _repair_notification_delivery_payload()
        _validate_existing_table("notificationdelivery", {
            "id", "event_id", "policy_id", "channel_id", "template_id", "payload_json",
            "dedupe_key", "status", "attempts", "next_attempt_at", "last_error", "sent_at",
            "created_at", "updated_at",
        })
    _ensure_index("ix_notification_delivery_event_channel", "notificationdelivery", ["event_id", "channel_id"])
    _ensure_index("ix_notification_delivery_status_next_attempt", "notificationdelivery", ["status", "next_attempt_at"])
    for column in ("channel_id", "created_at", "event_id", "next_attempt_at", "policy_id", "sent_at", "status", "template_id", "updated_at"):
        _ensure_index(f"ix_notificationdelivery_{column}", "notificationdelivery", [column])

    inspector = sa.inspect(op.get_bind())
    if "appsetting" in inspector.get_table_names():
        rows = op.get_bind().execute(sa.text("SELECT key, value FROM appsetting")).fetchall()
        values = {str(row[0]): row[1] for row in rows}
        config = {
            "host": values.get("smtp_host") or "",
            "port": values.get("smtp_port") or "25",
            "user": values.get("smtp_user") or "",
            "from": values.get("smtp_from") or "",
            "to": values.get("smtp_to") or "",
            "starttls": True,
        }
        encrypted_password = values.get("smtp_pass")
        complete = bool(config["host"] and config["port"] and config["user"] and config["from"] and config["to"] and encrypted_password)
        now = datetime.utcnow()
        channel_table = sa.table(
            "notificationchannel",
            sa.column("id", sa.Integer()),
            sa.column("name", sa.String()),
            sa.column("channel_type", sa.String()),
            sa.column("enabled", sa.Boolean()),
            sa.column("config_json", sa.Text()),
            sa.column("secret_encrypted", sa.Text()),
            sa.column("builtin_key", sa.String()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        channel_id = op.get_bind().execute(
            sa.text("SELECT id FROM notificationchannel WHERE builtin_key = :builtin_key"),
            {"builtin_key": "legacy_smtp"},
        ).scalar_one_or_none()
        if channel_id is None:
            op.get_bind().execute(
                channel_table.insert().values(
                    name="Default SMTP",
                    channel_type="smtp",
                    enabled=True,
                    config_json=json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                    secret_encrypted=encrypted_password,
                    builtin_key="legacy_smtp",
                    created_at=now,
                    updated_at=now,
                )
            )
            channel_id = op.get_bind().execute(
                sa.text("SELECT id FROM notificationchannel WHERE builtin_key = :builtin_key"),
                {"builtin_key": "legacy_smtp"},
            ).scalar_one()
        channel_id = int(channel_id)
        policy_table = sa.table(
            "notificationpolicy",
            sa.column("name", sa.String()),
            sa.column("enabled", sa.Boolean()),
            sa.column("priority", sa.Integer()),
            sa.column("event_types_json", sa.Text()),
            sa.column("group_ids_json", sa.Text()),
            sa.column("include_descendants", sa.Boolean()),
            sa.column("platforms_json", sa.Text()),
            sa.column("failure_types_json", sa.Text()),
            sa.column("channel_ids_json", sa.Text()),
            sa.column("template_id", sa.Integer()),
            sa.column("stop_processing", sa.Boolean()),
            sa.column("builtin_key", sa.String()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        definitions = (
            ("Backup failures", "legacy_failure", 900, ["backup_failed", "backup_summary", "task_cancelled"], "alert_on_fail"),
            ("Configuration changes", "legacy_config_change", 901, ["config_changed", "backup_summary"], "alert_on_config_change"),
            ("Backup summaries", "legacy_summary", 902, ["backup_summary", "task_cancelled"], "always_send_summary"),
        )
        for name, builtin_key, priority, event_types, setting_key in definitions:
            policy_id = op.get_bind().execute(
                sa.text("SELECT id FROM notificationpolicy WHERE builtin_key = :builtin_key"),
                {"builtin_key": builtin_key},
            ).scalar_one_or_none()
            if policy_id is not None:
                continue
            op.get_bind().execute(
                policy_table.insert().values(
                    name=name,
                    enabled=values.get(setting_key) == "1",
                    priority=priority,
                    event_types_json=json.dumps(event_types, separators=(",", ":")),
                    group_ids_json="[]",
                    include_descendants=True,
                    platforms_json="[]",
                    failure_types_json="[]",
                    channel_ids_json=json.dumps([channel_id]),
                    template_id=None,
                    stop_processing=False,
                    builtin_key=builtin_key,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    op.drop_table("notificationdelivery")
    op.drop_table("notificationevent")
    op.drop_table("notificationpolicy")
    op.drop_table("notificationtemplate")
    op.drop_table("notificationchannel")
