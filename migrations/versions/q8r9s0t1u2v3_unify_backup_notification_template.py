"""unify backup notifications around an editable detailed template

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-07-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q8r9s0t1u2v3"
down_revision: Union[str, Sequence[str], None] = "p7q8r9s0t1u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_MARKER = "The original detailed EasyNetBak email renderer is used at delivery time."
_SUBJECT = "{{ summary_subject }}"
_BODY = """<!doctype html>
<html lang="{{ locale }}">
<body style="font-family:Arial,Helvetica,sans-serif;color:#24324a;line-height:1.55">
  <h2 style="margin:0 0 16px">{{ labels["title"] }}</h2>
  <p><strong>{{ labels["task_time"] }}:</strong> {{ task_time }}</p>
  <p><strong>{{ labels["result"] }}:</strong>
    {{ labels["total"] }} {{ total_count }} {{ labels["unit"] }},
    {{ labels["succeeded"] }} <span style="color:#198754">{{ success_count }}</span> {{ labels["unit"] }},
    {{ labels["failed"] }} <span style="color:#dc3545">{{ failed_count }}</span> {{ labels["unit"] }},
    {{ labels["cancelled"] }} <span style="color:#d98a18">{{ cancelled_count }}</span> {{ labels["unit"] }}
  </p>
  {% if failed_count > 0 %}
  <h3 style="color:#dc3545">{{ labels["failed_section"] }}</h3>
  <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:1000px">
    <tr style="background:#f2f4f7"><th>{{ labels["device_name"] }}</th><th>{{ labels["device_host"] }}</th><th>{{ labels["duration"] }}</th><th>{{ labels["failure_type"] }}</th><th>{{ labels["error"] }}</th></tr>
    {% for item in items %}{% if not item["success"] and not item["cancelled"] %}<tr><td>{{ item["device_name"] }}</td><td>{{ item["device_host"] }}</td><td>{{ item["duration"] }}</td><td>{{ item["failure_type"] }}</td><td style="color:#dc3545">{{ item["error_message"] }}</td></tr>{% endif %}{% endfor %}
  </table>
  {% endif %}
  {% if cancelled_count > 0 %}
  <h3 style="color:#d98a18">{{ labels["cancelled_section"] }}</h3>
  <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:1000px">
    <tr style="background:#f2f4f7"><th>{{ labels["device_name"] }}</th><th>{{ labels["device_host"] }}</th><th>{{ labels["details"] }}</th></tr>
    {% for item in items %}{% if item["cancelled"] %}<tr><td>{{ item["device_name"] }}</td><td>{{ item["device_host"] }}</td><td>{{ item["error_message"] }}</td></tr>{% endif %}{% endfor %}
  </table>
  {% endif %}
  {% if changed_count > 0 %}
  <h3 style="color:#d98a18">{{ labels["changed_section"] }}</h3>
  <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:1000px">
    <tr style="background:#f2f4f7"><th>{{ labels["device_name"] }}</th><th>{{ labels["device_host"] }}</th><th>{{ labels["change_summary"] }}</th></tr>
    {% for item in items %}{% if item["changed"] %}<tr><td>{{ item["device_name"] }}</td><td>{{ item["device_host"] }}</td><td><div style="font-weight:bold;margin-bottom:4px">{{ labels["diff_rules_applied"] }}</div>{% if item["change_context_label"] %}<div style="font-weight:bold;margin-bottom:4px">{{ item["change_context_label"] }}</div>{% endif %}{% if item["change_lines"] %}<ul style="margin:0;padding-left:18px">{% for line in item["change_lines"] %}<li><code style="color:{% if line["kind"] == "add" %}#198754{% elif line["kind"] == "del" %}#dc3545{% else %}#6c757d{% endif %}">{{ line["prefix"] }} {{ line["text"] }}</code></li>{% endfor %}</ul>{% else %}{{ labels["changed_detail"] }}{% endif %}{% if item["change_truncated_label"] %}<div style="margin-top:6px;color:#6c757d;font-size:12px">{{ item["change_truncated_label"] }}</div>{% endif %}</td></tr>{% endif %}{% endfor %}
  </table>
  {% endif %}
</body>
</html>"""


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"notificationchannel", "notificationtemplate", "notificationpolicy"}.issubset(tables):
        raise RuntimeError("Notification routing tables are missing; apply earlier notification migrations first.")
    bind.execute(
        sa.text("UPDATE notificationchannel SET builtin_key = 'default_smtp' WHERE builtin_key = 'legacy_smtp'")
    )
    key_updates = {
        "legacy_failure": "builtin_failure",
        "legacy_config_change": "builtin_config_change",
        "legacy_summary": "builtin_summary",
    }
    for old_key, new_key in key_updates.items():
        bind.execute(
            sa.text("UPDATE notificationpolicy SET builtin_key = :new_key WHERE builtin_key = :old_key"),
            {"old_key": old_key, "new_key": new_key},
        )
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET builtin_key = 'builtin_backup_detailed', renderer_key = NULL "
            "WHERE builtin_key = 'legacy_detailed_email'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET name = :name, event_type = '*', channel_type = '*', "
            "subject_template = :subject, body_template = :body, content_type = 'html', renderer_key = NULL "
            "WHERE builtin_key = 'builtin_backup_detailed' AND body_template = :old_marker"
        ),
        {"name": "Detailed backup summary", "subject": _SUBJECT, "body": _BODY, "old_marker": _OLD_MARKER},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET name = 'Legacy detailed email', channel_type = 'smtp', "
            "subject_template = '', body_template = :old_marker, renderer_key = 'legacy_detailed_email' "
            "WHERE builtin_key = 'builtin_backup_detailed' AND body_template = :body"
        ),
        {"old_marker": _OLD_MARKER, "body": _BODY},
    )
    bind.execute(
        sa.text("UPDATE notificationtemplate SET builtin_key = 'legacy_detailed_email' WHERE builtin_key = 'builtin_backup_detailed'")
    )
    key_updates = {
        "builtin_failure": "legacy_failure",
        "builtin_config_change": "legacy_config_change",
        "builtin_summary": "legacy_summary",
    }
    for new_key, old_key in key_updates.items():
        bind.execute(
            sa.text("UPDATE notificationpolicy SET builtin_key = :old_key WHERE builtin_key = :new_key"),
            {"old_key": old_key, "new_key": new_key},
        )
    bind.execute(
        sa.text("UPDATE notificationchannel SET builtin_key = 'legacy_smtp' WHERE builtin_key = 'default_smtp'")
    )
