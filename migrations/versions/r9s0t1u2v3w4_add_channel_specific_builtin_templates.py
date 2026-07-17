"""add channel-specific built-in notification templates

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-07-17 16:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r9s0t1u2v3w4"
down_revision: Union[str, Sequence[str], None] = "q8r9s0t1u2v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EMAIL_KEY = "builtin_backup_detailed"
_ROBOT_KEY = "builtin_backup_robot"
_WEBHOOK_KEY = "builtin_backup_webhook_json"
_SUBJECT = "{{ summary_subject }}"
_ROBOT_BODY = """{{ labels["task_time"] }}: {{ task_time }}
{{ labels["result"] }}: {{ labels["total"] }} {{ total_count }} {{ labels["unit"] }}, {{ labels["succeeded"] }} {{ success_count }}, {{ labels["failed"] }} {{ failed_count }}, {{ labels["cancelled"] }} {{ cancelled_count }}

{% if failed_count > 0 %}[{{ labels["failed_section"] }}]
{% for item in items %}{% if not item["success"] and not item["cancelled"] %}- {{ item["device_name"] }} ({{ item["device_host"] }}) | {{ item["failure_type"] }} | {{ item["error_message"] }}
{% endif %}{% endfor %}{% endif %}
{% if cancelled_count > 0 %}[{{ labels["cancelled_section"] }}]
{% for item in items %}{% if item["cancelled"] %}- {{ item["device_name"] }} ({{ item["device_host"] }}) | {{ item["error_message"] }}
{% endif %}{% endfor %}{% endif %}
{% if changed_count > 0 %}[{{ labels["changed_section"] }}]
{% for item in items %}{% if item["changed"] %}- {{ item["device_name"] }} ({{ item["device_host"] }})
{% if item["change_context_label"] %}  {{ item["change_context_label"] }}
{% endif %}{% for line in item["change_lines"] %}  {{ line["prefix"] }} {{ line["text"] }}
{% endfor %}{% if item["change_truncated_label"] %}  {{ item["change_truncated_label"] }}
{% endif %}{% endif %}{% endfor %}{% endif %}"""
_WEBHOOK_BODY = """{
  "event": {{ event["type"]|tojson }},
  "title": {{ summary_subject|tojson }},
  "task_time": {{ task_time|tojson }},
  "summary": {
    "total": {{ total_count }},
    "succeeded": {{ success_count }},
    "failed": {{ failed_count }},
    "cancelled": {{ cancelled_count }},
    "changed": {{ changed_count }}
  },
  "items": {{ items|tojson }}
}"""


def _insert_template(*, name: str, channel_type: str, content_type: str, builtin_key: str, body: str) -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": builtin_key},
    ).scalar_one_or_none()
    if existing is not None:
        bind.execute(
            sa.text(
                "UPDATE notificationtemplate SET channel_type = :channel_type, content_type = :content_type, "
                "renderer_key = NULL, updated_at = :updated_at WHERE id = :id"
            ),
            {
                "channel_type": channel_type,
                "content_type": content_type,
                "updated_at": datetime.utcnow(),
                "id": int(existing),
            },
        )
        return
    candidate_name = name
    suffix = 2
    while bind.execute(
        sa.text("SELECT id FROM notificationtemplate WHERE name = :name"),
        {"name": candidate_name},
    ).scalar_one_or_none() is not None:
        candidate_name = f"{name} ({suffix})"
        suffix += 1
    now = datetime.utcnow()
    bind.execute(
        sa.text(
            "INSERT INTO notificationtemplate "
            "(name, enabled, event_type, channel_type, locale, subject_template, body_template, "
            "content_type, builtin_key, renderer_key, created_at, updated_at) "
            "VALUES (:name, :enabled, '*', :channel_type, 'zh-CN', :subject, :body, "
            ":content_type, :builtin_key, NULL, :created_at, :updated_at)"
        ),
        {
            "name": candidate_name,
            "enabled": True,
            "channel_type": channel_type,
            "subject": _SUBJECT,
            "body": body,
            "content_type": content_type,
            "builtin_key": builtin_key,
            "created_at": now,
            "updated_at": now,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "notificationtemplate" not in tables:
        raise RuntimeError("Notification template table is missing; apply earlier notification migrations first.")
    email_row = bind.execute(
        sa.text("SELECT id, name FROM notificationtemplate WHERE builtin_key = :builtin_key"),
        {"builtin_key": _EMAIL_KEY},
    ).mappings().first()
    email_name = email_row["name"] if email_row else "Email notification template - Detailed backup summary"
    if email_row and email_name in {"Detailed backup summary", "备份详细汇总"}:
        name_conflict = bind.execute(
            sa.text("SELECT id FROM notificationtemplate WHERE name = :name AND id != :id"),
            {"name": "Email notification template - Detailed backup summary", "id": int(email_row["id"])},
        ).scalar_one_or_none()
        if name_conflict is None:
            email_name = "Email notification template - Detailed backup summary"
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET name = :name, "
            "channel_type = 'smtp', content_type = 'html', renderer_key = NULL, updated_at = :updated_at "
            "WHERE builtin_key = :builtin_key"
        ),
        {
            "name": email_name,
            "updated_at": datetime.utcnow(),
            "builtin_key": _EMAIL_KEY,
        },
    )
    _insert_template(
        name="Robot notification template - Detailed backup summary",
        channel_type="robot",
        content_type="text",
        builtin_key=_ROBOT_KEY,
        body=_ROBOT_BODY,
    )
    _insert_template(
        name="Webhook notification template - Detailed backup summary",
        channel_type="webhook",
        content_type="json",
        builtin_key=_WEBHOOK_KEY,
        body=_WEBHOOK_BODY,
    )


def downgrade() -> None:
    bind = op.get_bind()
    email_id = bind.execute(
        sa.text("SELECT id FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": _EMAIL_KEY},
    ).scalar_one_or_none()
    if email_id is not None:
        for table in ("notificationpolicy", "notificationdelivery"):
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET template_id = :email_id WHERE template_id IN ("
                    "SELECT id FROM notificationtemplate WHERE builtin_key IN (:robot_key, :webhook_key))"
                ),
                {
                    "email_id": int(email_id),
                    "robot_key": _ROBOT_KEY,
                    "webhook_key": _WEBHOOK_KEY,
                },
            )
    bind.execute(
        sa.text("DELETE FROM notificationtemplate WHERE builtin_key IN (:robot_key, :webhook_key)"),
        {"robot_key": _ROBOT_KEY, "webhook_key": _WEBHOOK_KEY},
    )
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET "
            "name = CASE WHEN name = :new_name THEN :old_name ELSE name END, "
            "channel_type = '*', content_type = 'html', updated_at = :updated_at "
            "WHERE builtin_key = :builtin_key"
        ),
        {
            "new_name": "Email notification template - Detailed backup summary",
            "old_name": "Detailed backup summary",
            "updated_at": datetime.utcnow(),
            "builtin_key": _EMAIL_KEY,
        },
    )
