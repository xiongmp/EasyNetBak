"""upgrade the default robot markdown template to tables

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-07-17 20:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v3w4x5y6z7a8"
down_revision: Union[str, Sequence[str], None] = "u2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILTIN_KEY = "builtin_backup_robot"
_OLD_BODY = """**{{ labels["task_time"] }}**: {{ task_time }}

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
_NEW_BODY = """**{{ labels["task_time"] }}**: {{ task_time }}

**{{ labels["result"] }}**: {{ labels["total"] }} {{ total_count }} {{ labels["unit"] }} · {{ labels["succeeded"] }} **{{ success_count }}** · {{ labels["failed"] }} **{{ failed_count }}** · {{ labels["cancelled"] }} **{{ cancelled_count }}**

{% if failed_count > 0 %}### {{ labels["failed_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} | {{ labels["duration"] }} | {{ labels["failure_type"] }} | {{ labels["error"] }} |
| --- | --- | --- | --- | --- |
{% for item in items %}{% if not item["success"] and not item["cancelled"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` | {{ item["duration"]|mdescape }} | `{{ item["failure_type"]|mdescape }}` | {{ item["error_message"]|mdescape }} |
{% endif %}{% endfor %}{% endif %}
{% if cancelled_count > 0 %}### {{ labels["cancelled_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} | {{ labels["details"] }} |
| --- | --- | --- |
{% for item in items %}{% if item["cancelled"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` | {{ item["error_message"]|mdescape }} |
{% endif %}{% endfor %}{% endif %}
{% if changed_count > 0 %}### {{ labels["changed_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} | {{ labels["change_summary"] }} |
| --- | --- | --- |
{% for item in items %}{% if item["changed"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` | {{ labels["diff_rules_applied"]|mdescape }}{% if item["change_context_label"] %} · {{ item["change_context_label"]|mdescape }}{% endif %}{% for line in item["change_lines"] %} · `{{ line["prefix"]|mdescape }} {{ line["text"]|mdescape }}`{% endfor %}{% if item["change_truncated_label"] %} · {{ item["change_truncated_label"]|mdescape }}{% endif %} |
{% endif %}{% endfor %}{% endif %}"""


def _replace_body(expected: str, replacement: str) -> None:
    bind = op.get_bind()
    if "notificationtemplate" not in sa.inspect(bind).get_table_names():
        raise RuntimeError("Notification template table is missing; apply earlier notification migrations first.")
    bind.execute(
        sa.text(
            "UPDATE notificationtemplate SET body_template = :replacement, updated_at = :updated_at "
            "WHERE builtin_key = :builtin_key AND body_template = :expected"
        ),
        {
            "replacement": replacement,
            "updated_at": datetime.utcnow(),
            "builtin_key": _BUILTIN_KEY,
            "expected": expected,
        },
    )


def upgrade() -> None:
    _replace_body(_OLD_BODY, _NEW_BODY)


def downgrade() -> None:
    _replace_body(_NEW_BODY, _OLD_BODY)
