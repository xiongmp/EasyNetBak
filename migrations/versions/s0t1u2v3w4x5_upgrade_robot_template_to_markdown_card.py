"""upgrade the shared robot template to markdown card content

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-07-17 17:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s0t1u2v3w4x5"
down_revision: Union[str, Sequence[str], None] = "r9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILTIN_KEY = "builtin_backup_robot"
_OLD_NAME = "Robot notification template - Detailed backup summary"
_NEW_NAME = "Markdown notification template - Detailed backup summary"
_OLD_BODY = """{{ labels["task_time"] }}: {{ task_time }}
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
_NEW_BODY = """**{{ labels["task_time"] }}**: {{ task_time }}

**{{ labels["result"] }}**: {{ labels["total"] }} {{ total_count }} {{ labels["unit"] }} · {{ labels["succeeded"] }} **{{ success_count }}** · {{ labels["failed"] }} **{{ failed_count }}** · {{ labels["cancelled"] }} **{{ cancelled_count }}**

{% if failed_count > 0 %}### {{ labels["failed_section"] }}
{% for item in items %}{% if not item["success"] and not item["cancelled"] %}- **{{ item["device_name"]|mdescape }}** (`{{ item["device_host"]|mdescape }}`) · `{{ item["failure_type"]|mdescape }}`
  - {{ item["error_message"]|mdescape }}
{% endif %}{% endfor %}{% endif %}
{% if cancelled_count > 0 %}### {{ labels["cancelled_section"] }}
{% for item in items %}{% if item["cancelled"] %}- **{{ item["device_name"]|mdescape }}** (`{{ item["device_host"]|mdescape }}`) · {{ item["error_message"]|mdescape }}
{% endif %}{% endfor %}{% endif %}
{% if changed_count > 0 %}### {{ labels["changed_section"] }}
{% for item in items %}{% if item["changed"] %}- **{{ item["device_name"]|mdescape }}** (`{{ item["device_host"]|mdescape }}`)
{% if item["change_context_label"] %}  - {{ item["change_context_label"]|mdescape }}
{% endif %}{% for line in item["change_lines"] %}  - `{{ line["prefix"]|mdescape }} {{ line["text"]|mdescape }}`
{% endfor %}{% if item["change_truncated_label"] %}  - {{ item["change_truncated_label"]|mdescape }}
{% endif %}{% endif %}{% endfor %}{% endif %}"""


def upgrade() -> None:
    bind = op.get_bind()
    if "notificationtemplate" not in sa.inspect(bind).get_table_names():
        raise RuntimeError("Notification template table is missing; apply earlier notification migrations first.")
    row = bind.execute(
        sa.text("SELECT id, name, body_template FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": _BUILTIN_KEY},
    ).mappings().first()
    if row is None:
        return
    name = row["name"]
    if name in {_OLD_NAME, "机器人通知模板-备份详细汇总"}:
        conflict = bind.execute(
            sa.text("SELECT id FROM notificationtemplate WHERE name = :name AND id != :id"),
            {"name": _NEW_NAME, "id": int(row["id"])},
        ).scalar_one_or_none()
        if conflict is None:
            name = _NEW_NAME
    body = _NEW_BODY if row["body_template"] == _OLD_BODY else row["body_template"]
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET name = :name, channel_type = 'robot', content_type = 'markdown', "
            "body_template = :body, updated_at = :updated_at WHERE id = :id"
        ),
        {"name": name, "body": body, "updated_at": datetime.utcnow(), "id": int(row["id"])},
    )


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT id, name, body_template FROM notificationtemplate WHERE builtin_key = :key"),
        {"key": _BUILTIN_KEY},
    ).mappings().first()
    if row is None:
        return
    name = _OLD_NAME if row["name"] == _NEW_NAME else row["name"]
    body = _OLD_BODY if row["body_template"] == _NEW_BODY else row["body_template"]
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET name = :name, channel_type = 'robot', content_type = 'text', "
            "body_template = :body, updated_at = :updated_at WHERE id = :id"
        ),
        {"name": name, "body": body, "updated_at": datetime.utcnow(), "id": int(row["id"])},
    )
