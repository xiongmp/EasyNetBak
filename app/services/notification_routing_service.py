from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from jinja2 import StrictUndefined, TemplateError, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import crud
from app.core.settings import settings
from app.core.time import format_local_datetime, parse_timezone_offset_to_minutes
from app.i18n import translate
from app.i18n.email import render_email_template
from app.i18n.validators import normalize_locale
from app.models import (
    AppSetting,
    NotificationChannel,
    NotificationDelivery,
    NotificationEvent,
    NotificationPolicy,
    NotificationTemplate,
)
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.errors import ServiceError
from app.services.notification_service import send_email


logger = logging.getLogger(__name__)

CHANNEL_TYPES = ("smtp", "wecom", "dingtalk", "feishu", "webhook")
ROBOT_CHANNEL_TYPES = ("wecom", "dingtalk", "feishu")
TEMPLATE_CHANNEL_TYPES = (*CHANNEL_TYPES, "robot")
EVENT_TYPES = ("backup_failed", "config_changed", "backup_summary", "task_cancelled")
BUILTIN_POLICY_EVENT_TYPES = {
    "failure": ("backup_failed", "task_cancelled"),
    "config_change": ("config_changed",),
    "summary": ("backup_summary",),
}
CONTENT_TYPES = ("html", "markdown", "text", "json")
FAILURE_TYPES = (
    "TIMEOUT", "AUTH_FAILED", "REFUSED", "READ_TIMEOUT", "DISCONNECTED",
    "KEY_ERROR", "PERMISSION_DENIED", "PRIVILEGE_FAILED", "PROTOCOL_ERROR",
    "PROMPT_ERROR", "DNS_ERROR", "NETWORK_UNREACHABLE", "ALGO_MISMATCH",
    "SESSION_LIMIT", "DEVICE_NOT_FOUND", "TEMPLATE_NOT_FOUND", "PLATFORM_MISMATCH",
    "WORKER_UNAVAILABLE", "REDIS_SEMAPHORE_FULL", "REDIS_SEMAPHORE_UNAVAILABLE",
    "TASK_FAILURE", "TASK_REVOKED", "TIME_LIMIT", "ENQUEUE_FAILED", "CANCELLED", "UNKNOWN",
)
MAX_TEMPLATE_SIZE = 100_000
MAX_RENDERED_SIZE = 500_000
FEISHU_CARD_MAX_BYTES = 30 * 1024
FEISHU_TABLE_ROW_LIMIT = 20
MAX_ATTEMPTS = 5
BUILTIN_DETAILED_TEMPLATE_KEY = "legacy_detailed_email"
BUILTIN_DETAILED_TEMPLATE_KEY_V2 = "builtin_backup_detailed"
BUILTIN_ROBOT_TEMPLATE_KEY = "builtin_backup_robot"
BUILTIN_FEISHU_TEMPLATE_KEY = "builtin_backup_feishu"
BUILTIN_WEBHOOK_TEMPLATE_KEY = "builtin_backup_webhook_json"
BUILTIN_BACKUP_TEMPLATE_KEYS = {
    BUILTIN_DETAILED_TEMPLATE_KEY_V2,
    BUILTIN_ROBOT_TEMPLATE_KEY,
    BUILTIN_FEISHU_TEMPLATE_KEY,
    BUILTIN_WEBHOOK_TEMPLATE_KEY,
}
BUILTIN_TEMPLATE_CHANNEL_KEYS = {
    "smtp": BUILTIN_DETAILED_TEMPLATE_KEY_V2,
    "wecom": BUILTIN_ROBOT_TEMPLATE_KEY,
    "dingtalk": BUILTIN_ROBOT_TEMPLATE_KEY,
    "feishu": BUILTIN_FEISHU_TEMPLATE_KEY,
    "webhook": BUILTIN_WEBHOOK_TEMPLATE_KEY,
}
BUILTIN_TEMPLATE_FORMATS = {
    BUILTIN_DETAILED_TEMPLATE_KEY_V2: ("smtp", "html"),
    BUILTIN_ROBOT_TEMPLATE_KEY: ("robot", "markdown"),
    BUILTIN_FEISHU_TEMPLATE_KEY: ("feishu", "json"),
    BUILTIN_WEBHOOK_TEMPLATE_KEY: ("webhook", "json"),
}
BUILTIN_TEMPLATE_HINT_KEYS = {
    BUILTIN_DETAILED_TEMPLATE_KEY_V2: "notification.template.email_detailed_hint",
    BUILTIN_ROBOT_TEMPLATE_KEY: "notification.template.robot_detailed_hint",
    BUILTIN_FEISHU_TEMPLATE_KEY: "notification.template.feishu_compact_hint",
    BUILTIN_WEBHOOK_TEMPLATE_KEY: "notification.template.webhook_detailed_hint",
}
DEFAULT_SMTP_CHANNEL_KEY = "default_smtp"
BUILTIN_POLICY_KEYS = {
    "failure": "builtin_failure",
    "config_change": "builtin_config_change",
    "summary": "builtin_summary",
}
BUILTIN_NOTIFICATION_SCHEMA_SETTING = "notification_builtin_schema_version"
BUILTIN_NOTIFICATION_SCHEMA_VERSION = "2"
BUILTIN_NAME_SPECS = {
    ("channel", DEFAULT_SMTP_CHANNEL_KEY): ("Default SMTP", "notification.channel.builtin_smtp_name"),
    ("template", BUILTIN_DETAILED_TEMPLATE_KEY_V2): (
        "Email notification template - Detailed backup summary",
        "notification.template.legacy_detailed_name",
    ),
    ("template", BUILTIN_ROBOT_TEMPLATE_KEY): (
        "Markdown notification template - Detailed backup summary",
        "notification.template.robot_detailed_name",
    ),
    ("template", BUILTIN_FEISHU_TEMPLATE_KEY): (
        "Feishu notification template - Compact backup summary",
        "notification.template.feishu_compact_name",
    ),
    ("template", BUILTIN_WEBHOOK_TEMPLATE_KEY): (
        "Webhook notification template - Detailed backup summary",
        "notification.template.webhook_detailed_name",
    ),
    ("policy", BUILTIN_POLICY_KEYS["failure"]): (
        "Backup failures",
        "notification.policy.builtin_failure_name",
    ),
    ("policy", BUILTIN_POLICY_KEYS["config_change"]): (
        "Configuration changes",
        "notification.policy.builtin_config_change_name",
    ),
    ("policy", BUILTIN_POLICY_KEYS["summary"]): (
        "Backup summaries",
        "notification.policy.builtin_summary_name",
    ),
}
DETAILED_BACKUP_SUBJECT_TEMPLATE = "{{ summary_subject }}"
DETAILED_BACKUP_BODY_TEMPLATE = """<!doctype html>
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
    {% for item in items %}{% if not item["success"] and not item["cancelled"] %}
    <tr><td>{{ item["device_name"] }}</td><td>{{ item["device_host"] }}</td><td>{{ item["duration"] }}</td><td>{{ item["failure_type"] }}</td><td style="color:#dc3545">{{ item["error_message"] }}</td></tr>
    {% endif %}{% endfor %}
  </table>
  {% endif %}

  {% if cancelled_count > 0 %}
  <h3 style="color:#d98a18">{{ labels["cancelled_section"] }}</h3>
  <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:1000px">
    <tr style="background:#f2f4f7"><th>{{ labels["device_name"] }}</th><th>{{ labels["device_host"] }}</th><th>{{ labels["details"] }}</th></tr>
    {% for item in items %}{% if item["cancelled"] %}
    <tr><td>{{ item["device_name"] }}</td><td>{{ item["device_host"] }}</td><td>{{ item["error_message"] }}</td></tr>
    {% endif %}{% endfor %}
  </table>
  {% endif %}

  {% if changed_count > 0 %}
  <h3 style="color:#d98a18">{{ labels["changed_section"] }}</h3>
  <table border="1" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:1200px;border-color:#8a8f98">
    <tr style="background:#f7f8fa"><th style="padding:12px;text-align:center;width:18%">{{ labels["device_name"] }}</th><th style="padding:12px;text-align:center;width:20%">{{ labels["device_host"] }}</th><th style="padding:12px;text-align:center">{{ labels["change_summary"] }}</th></tr>
    {% for item in items %}{% if item["changed"] %}
    <tr><td style="padding:12px;vertical-align:middle;font-size:16px">{{ item["device_name"] }}</td><td style="padding:12px;vertical-align:middle;font-size:16px">{{ item["device_host"] }}</td><td style="padding:16px;vertical-align:top;line-height:1.65">
      <div style="font-weight:700;margin-bottom:4px">{{ labels["diff_rules_applied"] }}</div>
      {% if item["change_context_label"] %}<div style="font-weight:700;margin-bottom:6px">{{ item["change_context_label"] }}</div>{% endif %}
      {% if item["change_lines"] %}<ul style="margin:0;padding-left:24px">{% for line in item["change_lines"] %}
      <li style="margin:3px 0"><code style="white-space:pre-wrap;overflow-wrap:anywhere;color:{% if line["kind"] == "add" %}#198754{% elif line["kind"] == "del" %}#dc3545{% else %}#6c757d{% endif %}">{{ line["prefix"] }} {{ line["text"] }}</code></li>
      {% endfor %}</ul>{% else %}{{ labels["changed_detail"] }}{% endif %}
      {% if item["change_truncated_label"] %}<div style="margin-top:8px;color:#6c757d;font-size:12px;font-weight:600">{{ item["change_truncated_label"] }}</div>{% endif %}
    </td></tr>
    {% endif %}{% endfor %}
  </table>
  {% endif %}
</body>
</html>"""
MARKDOWN_BACKUP_BODY_TEMPLATE = """**{{ labels["task_time"] }}**: {{ task_time }}

**{{ labels["result"] }}**: {{ labels["total"] }} {{ total_count }} {{ labels["unit"] }} · {{ labels["succeeded"] }} **{{ success_count }}** · {{ labels["failed"] }} **{{ failed_count }}** · {{ labels["cancelled"] }} **{{ cancelled_count }}**

{% if failed_count > 0 %}### {{ labels["failed_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} | {{ labels["duration"] }} | {{ labels["failure_type"] }} |
| --- | --- | --- | --- |
{% for item in items %}{% if not item["success"] and not item["cancelled"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` | {{ item["duration"]|mdescape }} | `{{ item["failure_type"]|mdescape }}` |
{% endif %}{% endfor %}{% endif %}
{% if cancelled_count > 0 %}### {{ labels["cancelled_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} | {{ labels["details"] }} |
| --- | --- | --- |
{% for item in items %}{% if item["cancelled"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` | {{ item["error_message"]|mdescape }} |
{% endif %}{% endfor %}{% endif %}
{% if changed_count > 0 %}### {{ labels["changed_section"] }}
| {{ labels["device_name"] }} | {{ labels["device_host"] }} |
| --- | --- |
{% for item in items %}{% if item["changed"] %}| **{{ item["device_name"]|mdescape }}** | `{{ item["device_host"]|mdescape }}` |
{% endif %}{% endfor %}{% endif %}"""
FEISHU_BACKUP_BODY_TEMPLATE = """{
  "schema": "2.0",
  "config": {
    "width_mode": "fill",
    "summary": {"content": {{ summary_subject|tojson }}}
  },
  "header": {
    "template": {{ feishu_header_template|tojson }},
    "title": {"tag": "plain_text", "content": {{ summary_subject|tojson }}}
  },
  "body": {
    "direction": "vertical",
    "padding": "12px 12px 12px 12px",
    "elements": [
      {"tag": "markdown", "content": {{ feishu_summary_text|tojson }}}
      {% if failed_count > 0 %},
      {"tag": "markdown", "content": {{ feishu_failed_title|tojson }}},
      {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": {"background_style": "grey", "bold": true, "text_align": "left", "lines": 1},
        "columns": [
          {"name": "device_name", "display_name": {{ labels["device_name"]|tojson }}, "data_type": "text", "width": "auto"},
          {"name": "device_host", "display_name": {{ labels["device_host"]|tojson }}, "data_type": "text", "width": "auto"},
          {"name": "duration", "display_name": {{ labels["duration"]|tojson }}, "data_type": "text", "width": "auto"},
          {"name": "failure_type", "display_name": {{ labels["failure_type"]|tojson }}, "data_type": "text", "width": "auto"}
        ],
        "rows": {{ feishu_failed_rows_json }}
      }
      {% if feishu_failed_hidden_label %},
      {"tag": "markdown", "content": {{ feishu_failed_hidden_label|tojson }}}
      {% endif %}
      {% endif %}
      {% if cancelled_count > 0 %},
      {"tag": "markdown", "content": {{ feishu_cancelled_title|tojson }}},
      {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": {"background_style": "grey", "bold": true, "text_align": "left", "lines": 1},
        "columns": [
          {"name": "device_name", "display_name": {{ labels["device_name"]|tojson }}, "data_type": "text", "width": "auto"},
          {"name": "device_host", "display_name": {{ labels["device_host"]|tojson }}, "data_type": "text", "width": "auto"}
        ],
        "rows": {{ feishu_cancelled_rows_json }}
      }
      {% if feishu_cancelled_hidden_label %},
      {"tag": "markdown", "content": {{ feishu_cancelled_hidden_label|tojson }}}
      {% endif %}
      {% endif %}
      {% if changed_count > 0 %},
      {"tag": "markdown", "content": {{ feishu_changed_title|tojson }}},
      {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": {"background_style": "grey", "bold": true, "text_align": "left", "lines": 1},
        "columns": [
          {"name": "device_name", "display_name": {{ labels["device_name"]|tojson }}, "data_type": "text", "width": "auto"},
          {"name": "device_host", "display_name": {{ labels["device_host"]|tojson }}, "data_type": "text", "width": "auto"}
        ],
        "rows": {{ feishu_changed_rows_json }}
      }
      {% if feishu_changed_hidden_label %},
      {"tag": "markdown", "content": {{ feishu_changed_hidden_label|tojson }}}
      {% endif %}
      {% endif %}
    ]
  }
}"""
WEBHOOK_BACKUP_BODY_TEMPLATE = """{
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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class _TemplateSandbox(SandboxedEnvironment):
    def is_safe_attribute(self, obj: object, attr: str, value: object) -> bool:
        return False


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _builtin_name_values(entity: str, builtin_key: str | None) -> tuple[str, str, set[str]] | None:
    spec = BUILTIN_NAME_SPECS.get((entity, builtin_key or ""))
    if not spec:
        return None
    canonical, message_key = spec
    defaults = {
        canonical,
        translate("zh-CN", message_key),
        translate("en-US", message_key),
    }
    if entity == "template" and builtin_key == BUILTIN_DETAILED_TEMPLATE_KEY_V2:
        defaults.update({"Legacy detailed email", "原版详细邮件"})
        defaults.update({"Detailed backup summary", "备份详细汇总"})
    elif entity == "template" and builtin_key == BUILTIN_ROBOT_TEMPLATE_KEY:
        defaults.update({
            "Robot notification template - Detailed backup summary",
            "机器人通知模板-备份详细汇总",
        })
    return canonical, message_key, defaults


def _localized_builtin_name(entity: str, builtin_key: str | None, stored_name: str, locale: str) -> tuple[str, str]:
    values = _builtin_name_values(entity, builtin_key)
    if not values or stored_name not in values[2]:
        return stored_name, ""
    return translate(normalize_locale(locale), values[1]), values[1]


def _canonical_builtin_name(entity: str, builtin_key: str | None, submitted_name: str) -> str:
    values = _builtin_name_values(entity, builtin_key)
    if values and submitted_name.strip() in values[2]:
        return values[0]
    return submitted_name.strip()


def _normalize_builtin_template_name(session: Session, template: NotificationTemplate, builtin_key: str) -> None:
    values = _builtin_name_values("template", builtin_key)
    if not values or template.name not in values[2]:
        return
    canonical = values[0]
    conflict = session.exec(
        select(NotificationTemplate).where(
            NotificationTemplate.name == canonical,
            NotificationTemplate.id != template.id,
        )
    ).first()
    if conflict is None:
        template.name = canonical


def _clean_csv(values: str | list[str] | None, *, lower: bool = False) -> list[str]:
    source = values if isinstance(values, list) else str(values or "").split(",")
    result: list[str] = []
    for value in source:
        item = str(value).strip()
        if lower:
            item = item.lower()
        if item and item not in result:
            result.append(item)
    return result


def _clean_ints(values: str | list[int] | None) -> list[int]:
    source = values if isinstance(values, list) else str(values or "").split(",")
    result: list[int] = []
    for value in source:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in result:
            result.append(item)
    return result


def _sanitize_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"https?://[^\s]+", "[redacted-url]", text)
    text = re.sub(r"(?i)(token|secret|password|key)=([^&\s]+)", r"\1=[redacted]", text)
    return text[:500] or exc.__class__.__name__


def _escape_markdown(value: Any) -> str:
    text = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|])", r"\\\1", text)


def _escape_markdown_code(value: Any) -> str:
    text = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def _template_environment(content_type: str) -> SandboxedEnvironment:
    environment = _TemplateSandbox(
        undefined=StrictUndefined,
        autoescape=select_autoescape(default=content_type == "html", default_for_string=content_type == "html"),
        enable_async=False,
    )
    allowed = {"escape", "e", "default", "join", "length", "lower", "upper", "replace", "tojson"}
    environment.filters = {key: value for key, value in environment.filters.items() if key in allowed}
    environment.filters["mdescape"] = _escape_markdown
    environment.filters["mdcode"] = _escape_markdown_code
    environment.globals = {}
    environment.tests = {}
    return environment


def render_custom_template(source: str, context: dict[str, Any], *, content_type: str) -> str:
    if len(source or "") > MAX_TEMPLATE_SIZE:
        raise ServiceError("Template is too large", code="NOTIFICATION_TEMPLATE_TOO_LARGE")
    try:
        rendered = _template_environment(content_type).from_string(source or "").render(**context)
    except TemplateError as exc:
        raise ServiceError(
            "Template rendering failed",
            code="NOTIFICATION_TEMPLATE_INVALID",
            context={"detail": _sanitize_error(exc)},
        ) from exc
    if len(rendered) > MAX_RENDERED_SIZE:
        raise ServiceError("Rendered message is too large", code="NOTIFICATION_MESSAGE_TOO_LARGE")
    return rendered


def _legacy_channel_config(session: Session) -> tuple[dict[str, Any], str | None]:
    config = {
        "host": crud.get_setting(session, key="smtp_host") or "",
        "port": crud.get_setting(session, key="smtp_port") or "25",
        "user": crud.get_setting(session, key="smtp_user") or "",
        "from": crud.get_setting(session, key="smtp_from") or "",
        "to": crud.get_setting(session, key="smtp_to") or "",
        "starttls": True,
    }
    return config, crud.get_setting(session, key="smtp_pass")


def ensure_builtin_defaults(session: Session) -> NotificationChannel:
    schema_setting = session.get(AppSetting, BUILTIN_NOTIFICATION_SCHEMA_SETTING)
    upgrade_builtins = not schema_setting or schema_setting.value != BUILTIN_NOTIFICATION_SCHEMA_VERSION
    channel = session.exec(
        select(NotificationChannel).where(NotificationChannel.builtin_key.in_([DEFAULT_SMTP_CHANNEL_KEY, "legacy_smtp"]))
    ).first()
    if channel is None:
        config, password_encrypted = _legacy_channel_config(session)
        complete = bool(config["host"] and config["port"] and config["user"] and config["from"] and config["to"] and password_encrypted)
        channel = NotificationChannel(
            name="Default SMTP",
            channel_type="smtp",
            enabled=complete,
            config_json=_dump(config),
            secret_encrypted=encrypt_secret(_dump({"password": decrypt_secret(password_encrypted) or ""})) if password_encrypted else None,
            builtin_key=DEFAULT_SMTP_CHANNEL_KEY,
        )
        session.add(channel)
        session.flush()
    else:
        channel.builtin_key = DEFAULT_SMTP_CHANNEL_KEY
        raw_secret = decrypt_secret(channel.secret_encrypted)
        if raw_secret and not _json_dict(raw_secret):
            channel.secret_encrypted = encrypt_secret(_dump({"password": raw_secret}))
        session.add(channel)
        session.flush()

    email_template = session.exec(
        select(NotificationTemplate).where(
            NotificationTemplate.builtin_key.in_([BUILTIN_DETAILED_TEMPLATE_KEY_V2, BUILTIN_DETAILED_TEMPLATE_KEY])
        )
    ).first()
    if email_template is None:
        email_template = NotificationTemplate(
            name="Email notification template - Detailed backup summary",
            event_type="*",
            channel_type="smtp",
            locale="zh-CN",
            subject_template=DETAILED_BACKUP_SUBJECT_TEMPLATE,
            body_template=DETAILED_BACKUP_BODY_TEMPLATE,
            content_type="html",
            builtin_key=BUILTIN_DETAILED_TEMPLATE_KEY_V2,
            renderer_key=None,
        )
    else:
        email_template.builtin_key = BUILTIN_DETAILED_TEMPLATE_KEY_V2
        _normalize_builtin_template_name(session, email_template, BUILTIN_DETAILED_TEMPLATE_KEY_V2)
        email_template.channel_type = "smtp"
        email_template.content_type = "html"
        email_template.renderer_key = None
        if upgrade_builtins:
            email_template.body_template = DETAILED_BACKUP_BODY_TEMPLATE
            email_template.updated_at = datetime.utcnow()
    session.add(email_template)
    session.flush()

    builtin_template_definitions = (
        (
            BUILTIN_ROBOT_TEMPLATE_KEY,
            "Markdown notification template - Detailed backup summary",
            "robot",
            "markdown",
            MARKDOWN_BACKUP_BODY_TEMPLATE,
        ),
        (
            BUILTIN_FEISHU_TEMPLATE_KEY,
            "Feishu notification template - Compact backup summary",
            "feishu",
            "json",
            FEISHU_BACKUP_BODY_TEMPLATE,
        ),
        (
            BUILTIN_WEBHOOK_TEMPLATE_KEY,
            "Webhook notification template - Detailed backup summary",
            "webhook",
            "json",
            WEBHOOK_BACKUP_BODY_TEMPLATE,
        ),
    )
    for builtin_key, name, channel_type, content_type, body_template in builtin_template_definitions:
        builtin_template = session.exec(
            select(NotificationTemplate).where(NotificationTemplate.builtin_key == builtin_key)
        ).first()
        if builtin_template is None:
            builtin_template = NotificationTemplate(
                name=name,
                event_type="*",
                channel_type=channel_type,
                locale="zh-CN",
                subject_template=DETAILED_BACKUP_SUBJECT_TEMPLATE,
                body_template=body_template,
                content_type=content_type,
                builtin_key=builtin_key,
                renderer_key=None,
            )
        else:
            _normalize_builtin_template_name(session, builtin_template, builtin_key)
            builtin_template.channel_type = channel_type
            builtin_template.content_type = content_type
            builtin_template.renderer_key = None
            if builtin_key in {BUILTIN_ROBOT_TEMPLATE_KEY, BUILTIN_FEISHU_TEMPLATE_KEY}:
                builtin_template.body_template = body_template
                builtin_template.updated_at = datetime.utcnow()
        session.add(builtin_template)
    session.flush()

    definitions = (
        (BUILTIN_POLICY_KEYS["failure"], "Backup failures", list(BUILTIN_POLICY_EVENT_TYPES["failure"]), "alert_on_fail", "legacy_failure"),
        (BUILTIN_POLICY_KEYS["config_change"], "Configuration changes", list(BUILTIN_POLICY_EVENT_TYPES["config_change"]), "alert_on_config_change", "legacy_config_change"),
        (BUILTIN_POLICY_KEYS["summary"], "Backup summaries", list(BUILTIN_POLICY_EVENT_TYPES["summary"]), "always_send_summary", "legacy_summary"),
    )
    for priority, (builtin_key, name, event_types, setting_key, old_key) in enumerate(definitions, start=900):
        policy = session.exec(
            select(NotificationPolicy).where(NotificationPolicy.builtin_key.in_([builtin_key, old_key]))
        ).first()
        if policy is None:
            policy = NotificationPolicy(
                name=name,
                enabled=crud.get_setting(session, key=setting_key) == "1",
                priority=priority,
                event_types_json=_dump(event_types),
                channel_ids_json=_dump([channel.id]),
                template_id=email_template.id,
                builtin_key=builtin_key,
            )
        else:
            policy.builtin_key = builtin_key
            if upgrade_builtins:
                policy.event_types_json = _dump(event_types)
                policy.updated_at = datetime.utcnow()
        session.add(policy)
    session.flush()
    if upgrade_builtins:
        crud.set_setting(
            session,
            key=BUILTIN_NOTIFICATION_SCHEMA_SETTING,
            value=BUILTIN_NOTIFICATION_SCHEMA_VERSION,
        )
    return channel


def list_channels(session: Session) -> list[NotificationChannel]:
    return list(session.exec(select(NotificationChannel).order_by(NotificationChannel.builtin_key.desc(), NotificationChannel.id)))


def list_templates(session: Session) -> list[NotificationTemplate]:
    return list(session.exec(select(NotificationTemplate).order_by(NotificationTemplate.name, NotificationTemplate.id)))


def list_policies(session: Session, *, include_builtin: bool = True) -> list[NotificationPolicy]:
    statement = select(NotificationPolicy)
    if not include_builtin:
        statement = statement.where(NotificationPolicy.builtin_key.is_(None))
    return list(session.exec(statement.order_by(NotificationPolicy.priority, NotificationPolicy.id)))


def has_custom_policy_for_event(session: Session, event_type: str) -> bool:
    return any(
        policy.enabled
        and not policy.builtin_key
        and event_type in _json_list(policy.event_types_json)
        for policy in list_policies(session)
    )


def has_unconditional_summary_policy(session: Session) -> bool:
    """Return whether a completed backup should always emit a summary."""
    return has_enabled_policy_for_event(session, "backup_summary")


def has_enabled_policy_for_event(session: Session, event_type: str) -> bool:
    ensure_builtin_defaults(session)
    return any(
        policy.enabled and event_type in _json_list(policy.event_types_json)
        for policy in list_policies(session)
    )


def is_builtin_policy_enabled(session: Session, kind: str) -> bool:
    ensure_builtin_defaults(session)
    key = BUILTIN_POLICY_KEYS.get(kind)
    if not key:
        return False
    policy = session.exec(select(NotificationPolicy).where(NotificationPolicy.builtin_key == key)).first()
    return bool(policy and policy.enabled)


def list_deliveries(session: Session, *, limit: int = 30, locale: str = "zh-CN") -> list[dict[str, Any]]:
    deliveries = list(
        session.exec(select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(max(1, min(limit, 100))))
    )
    channels = {item.id: item for item in session.exec(select(NotificationChannel))}
    events = {item.id: item for item in session.exec(
        select(NotificationEvent).where(NotificationEvent.id.in_([delivery.event_id for delivery in deliveries]))
    )} if deliveries else {}
    offset_minutes = parse_timezone_offset_to_minutes(
        crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    )
    if offset_minutes is None:
        offset_minutes = parse_timezone_offset_to_minutes(settings.timezone_offset) or 0
    rows: list[dict[str, Any]] = []
    for item in deliveries:
        channel = channels.get(item.channel_id)
        channel_name = "-"
        if channel:
            channel_name, _ = _localized_builtin_name("channel", channel.builtin_key, channel.name, locale)
        payload = _json_dict(item.payload_json)
        rows.append({
            "id": item.id,
            "channel_name": channel_name,
            "event_type": events[item.event_id].event_type if item.event_id in events else "",
            "subject": item.subject or str(payload.get("fallback_subject") or ""),
            "status": item.status,
            "attempts": item.attempts,
            "last_error": item.last_error or "",
            "created_at": format_local_datetime(item.created_at, offset_minutes=offset_minutes),
            "sent_at": format_local_datetime(item.sent_at, offset_minutes=offset_minutes),
        })
    return rows


def save_channel(
    session: Session,
    *,
    channel_id: int | None,
    name: str,
    channel_type: str,
    enabled: bool,
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> NotificationChannel:
    normalized_type = (channel_type or "").strip().lower()
    if normalized_type not in CHANNEL_TYPES:
        raise ServiceError("Unsupported notification channel", code="NOTIFICATION_CHANNEL_INVALID")
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ServiceError("Channel name is required", code="NOTIFICATION_CHANNEL_NAME_REQUIRED")
    channel = session.get(NotificationChannel, channel_id) if channel_id else None
    if channel_id and channel is None:
        raise ServiceError("Notification channel not found", code="NOTIFICATION_CHANNEL_NOT_FOUND", status_code=404)
    if channel is None:
        channel = NotificationChannel(name=normalized_name, channel_type=normalized_type)
    else:
        normalized_name = _canonical_builtin_name("channel", channel.builtin_key, normalized_name)
    existing_secrets = _channel_secrets(channel)
    for key, value in secrets.items():
        raw = str(value or "").strip()
        if raw and set(raw) != {"*"}:
            existing_secrets[key] = raw
    channel.name = normalized_name
    channel.channel_type = normalized_type
    channel.enabled = bool(enabled)
    channel.config_json = _dump(config)
    channel.secret_encrypted = encrypt_secret(_dump(existing_secrets)) if existing_secrets else None
    channel.updated_at = datetime.utcnow()
    session.add(channel)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ServiceError("Channel name already exists", code="NOTIFICATION_CHANNEL_CONFLICT", status_code=409) from exc
    return channel


def test_smtp_channel(
    session: Session,
    *,
    channel_id: int | None,
    config: dict[str, Any],
    password: str,
    subject: str,
    content: str,
    email_sender=send_email,
) -> bool:
    existing = session.get(NotificationChannel, channel_id) if channel_id else None
    secrets = _channel_secrets(existing) if existing else {}
    raw_password = str(password or "").strip()
    if raw_password and set(raw_password) != {"*"}:
        secrets["password"] = raw_password
    smtp_config = {
        "smtp_host": str(config.get("host") or "").strip(),
        "smtp_port": str(config.get("port") or "25").strip(),
        "smtp_user": str(config.get("user") or "").strip(),
        "smtp_pass": str(secrets.get("password") or ""),
        "smtp_from": str(config.get("from") or "").strip(),
        "smtp_to": str(config.get("to") or "").strip(),
    }
    if not all(smtp_config.values()):
        raise ServiceError("SMTP configuration is incomplete", code="NOTIFICATION_CHANNEL_SMTP_INCOMPLETE")
    return bool(email_sender(subject, content, content_type="html", smtp_config=smtp_config))


def test_channel(
    session: Session,
    *,
    channel_id: int | None,
    channel_type: str,
    config: dict[str, Any],
    secrets: dict[str, Any],
    subject: str,
    content: str,
    email_sender=send_email,
) -> bool:
    normalized_type = str(channel_type or "").strip().lower()
    existing = session.get(NotificationChannel, channel_id) if channel_id else None
    if channel_id and existing is None:
        raise ServiceError("Notification channel not found", code="NOTIFICATION_CHANNEL_NOT_FOUND", status_code=404)
    if existing and existing.channel_type != normalized_type:
        raise ServiceError("Notification channel type mismatch", code="NOTIFICATION_CHANNEL_TYPE_MISMATCH")
    if normalized_type == "smtp":
        return test_smtp_channel(
            session,
            channel_id=channel_id,
            config=config,
            password=str(secrets.get("password") or ""),
            subject=subject,
            content=content,
            email_sender=email_sender,
        )
    if normalized_type not in ROBOT_CHANNEL_TYPES:
        raise ServiceError("Channel testing is not supported", code="NOTIFICATION_CHANNEL_TEST_UNSUPPORTED")

    merged_secrets = _channel_secrets(existing) if existing else {}
    for key in ("url", "signing_secret", "authorization"):
        value = str(secrets.get(key) or "").strip()
        if value and set(value) != {"*"}:
            merged_secrets[key] = value
    if not str(merged_secrets.get("url") or "").strip():
        raise ServiceError("Webhook URL is required", code="NOTIFICATION_CHANNEL_WEBHOOK_REQUIRED")

    test_config = dict(_json_dict(existing.config_json) if existing else {})
    test_config.update({
        "timeout": max(1, min(int(config.get("timeout") or 10), 30)),
        "allow_private": bool(config.get("allow_private")),
    })
    test_channel = NotificationChannel(
        name=existing.name if existing else "Notification channel test",
        channel_type=normalized_type,
        enabled=True,
        config_json=_dump(test_config),
        secret_encrypted=encrypt_secret(_dump(merged_secrets)),
    )
    _send_channel(
        test_channel,
        subject=subject,
        body=content,
        content_type="markdown",
        payload={"event_type": "channel_test"},
        email_sender=email_sender,
    )
    return True


def delete_channel(session: Session, channel_id: int) -> None:
    channel = session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ServiceError("Notification channel not found", code="NOTIFICATION_CHANNEL_NOT_FOUND", status_code=404)
    if channel.builtin_key:
        raise ServiceError("Built-in channels cannot be deleted", code="NOTIFICATION_CHANNEL_BUILTIN")
    for policy in list_policies(session):
        ids = [item for item in _clean_ints(_json_list(policy.channel_ids_json)) if item != channel_id]
        if len(ids) != len(_json_list(policy.channel_ids_json)):
            policy.channel_ids_json = _dump(ids)
            policy.updated_at = datetime.utcnow()
            session.add(policy)
    session.delete(channel)
    session.flush()


def save_template(
    session: Session,
    *,
    template_id: int | None,
    name: str,
    enabled: bool,
    event_type: str,
    channel_type: str,
    locale: str,
    subject_template: str,
    body_template: str,
    content_type: str,
) -> NotificationTemplate:
    if content_type not in CONTENT_TYPES:
        raise ServiceError("Template selector is invalid", code="NOTIFICATION_TEMPLATE_SELECTOR_INVALID")
    if not name.strip() or not body_template.strip():
        raise ServiceError("Template name and body are required", code="NOTIFICATION_TEMPLATE_REQUIRED")
    template = session.get(NotificationTemplate, template_id) if template_id else None
    if template_id and template is None:
        raise ServiceError("Notification template not found", code="NOTIFICATION_TEMPLATE_NOT_FOUND", status_code=404)
    if template is None:
        template = NotificationTemplate(name=name.strip(), body_template=body_template)
    builtin_format = BUILTIN_TEMPLATE_FORMATS.get(template.builtin_key or "")
    if builtin_format:
        channel_type, content_type = builtin_format
    else:
        channel_type = {"html": "smtp", "markdown": "robot", "json": "webhook", "text": "*"}[content_type]
    event_type = "*"
    sample = sample_template_context(locale=locale, event_type=event_type)
    render_custom_template(subject_template or "Notification", sample, content_type="text")
    rendered = render_custom_template(body_template, sample, content_type=content_type)
    if content_type == "json":
        try:
            json.loads(rendered)
        except ValueError as exc:
            raise ServiceError("JSON template must render valid JSON", code="NOTIFICATION_TEMPLATE_JSON_INVALID") from exc
    template.name = _canonical_builtin_name("template", template.builtin_key, name)
    template.enabled = bool(enabled)
    template.event_type = event_type
    template.channel_type = channel_type
    template.locale = locale if locale in {"zh-CN", "en-US"} else "zh-CN"
    template.subject_template = subject_template.strip()
    template.body_template = body_template
    template.content_type = content_type
    template.renderer_key = None
    template.updated_at = datetime.utcnow()
    session.add(template)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ServiceError("Template name already exists", code="NOTIFICATION_TEMPLATE_CONFLICT", status_code=409) from exc
    return template


def delete_template(session: Session, template_id: int) -> None:
    template = session.get(NotificationTemplate, template_id)
    if template is None:
        raise ServiceError("Notification template not found", code="NOTIFICATION_TEMPLATE_NOT_FOUND", status_code=404)
    if template.builtin_key:
        raise ServiceError("Built-in templates cannot be deleted", code="NOTIFICATION_TEMPLATE_BUILTIN")
    if session.exec(select(NotificationPolicy).where(NotificationPolicy.template_id == template_id)).first():
        raise ServiceError("Template is used by a policy", code="NOTIFICATION_TEMPLATE_IN_USE", status_code=409)
    session.delete(template)
    session.flush()


def save_policy(
    session: Session,
    *,
    policy_id: int | None,
    name: str,
    enabled: bool,
    priority: int,
    event_types: list[str],
    group_ids: list[int],
    include_descendants: bool,
    platforms: list[str],
    failure_types: list[str],
    channel_ids: list[int],
    template_id: int | None,
    stop_processing: bool,
) -> NotificationPolicy:
    if not name.strip() or not event_types or not channel_ids:
        raise ServiceError("Policy name, events and channels are required", code="NOTIFICATION_POLICY_REQUIRED")
    if any(item not in EVENT_TYPES for item in event_types):
        raise ServiceError("Policy event is invalid", code="NOTIFICATION_POLICY_EVENT_INVALID")
    known_channels = {item.id for item in session.exec(select(NotificationChannel))}
    if any(item not in known_channels for item in channel_ids):
        raise ServiceError("Policy contains an unknown channel", code="NOTIFICATION_POLICY_CHANNEL_INVALID")
    selected_template = session.get(NotificationTemplate, template_id) if template_id else None
    if template_id and selected_template is None:
        raise ServiceError("Policy template does not exist", code="NOTIFICATION_POLICY_TEMPLATE_INVALID")
    selected_channels = list(session.exec(select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))))
    if selected_template and selected_template.builtin_key not in BUILTIN_BACKUP_TEMPLATE_KEYS and any(
        not _template_matches_channel(selected_template, channel.channel_type)
        for channel in selected_channels
    ):
        raise ServiceError("Policy template does not match every channel", code="NOTIFICATION_POLICY_TEMPLATE_MISMATCH")
    policy = session.get(NotificationPolicy, policy_id) if policy_id else None
    if policy_id and policy is None:
        raise ServiceError("Notification policy not found", code="NOTIFICATION_POLICY_NOT_FOUND", status_code=404)
    if policy is None:
        policy = NotificationPolicy(name=name.strip())
    policy.name = _canonical_builtin_name("policy", policy.builtin_key, name)
    policy.enabled = bool(enabled)
    policy.priority = max(0, min(int(priority), 9999))
    policy.event_types_json = _dump(event_types)
    policy.group_ids_json = _dump(group_ids)
    policy.include_descendants = bool(include_descendants)
    policy.platforms_json = _dump(_clean_csv(platforms, lower=True))
    policy.failure_types_json = _dump([item.upper() for item in _clean_csv(failure_types)])
    policy.channel_ids_json = _dump(channel_ids)
    policy.template_id = template_id
    policy.stop_processing = bool(stop_processing)
    policy.updated_at = datetime.utcnow()
    session.add(policy)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ServiceError("Policy name already exists", code="NOTIFICATION_POLICY_CONFLICT", status_code=409) from exc
    return policy


def set_channel_enabled(session: Session, channel_id: int, enabled: bool) -> NotificationChannel:
    channel = session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ServiceError("Notification channel not found", code="NOTIFICATION_CHANNEL_NOT_FOUND", status_code=404)
    channel.enabled = bool(enabled)
    channel.updated_at = datetime.utcnow()
    session.add(channel)
    session.flush()
    return channel


def set_template_enabled(session: Session, template_id: int, enabled: bool) -> NotificationTemplate:
    template = session.get(NotificationTemplate, template_id)
    if template is None:
        raise ServiceError("Notification template not found", code="NOTIFICATION_TEMPLATE_NOT_FOUND", status_code=404)
    template.enabled = bool(enabled)
    template.updated_at = datetime.utcnow()
    session.add(template)
    session.flush()
    return template


def set_policy_enabled(session: Session, policy_id: int, enabled: bool) -> NotificationPolicy:
    policy = session.get(NotificationPolicy, policy_id)
    if policy is None:
        raise ServiceError("Notification policy not found", code="NOTIFICATION_POLICY_NOT_FOUND", status_code=404)
    policy.enabled = bool(enabled)
    policy.updated_at = datetime.utcnow()
    session.add(policy)
    session.flush()
    return policy


def delete_policy(session: Session, policy_id: int) -> None:
    policy = session.get(NotificationPolicy, policy_id)
    if policy is None:
        raise ServiceError("Notification policy not found", code="NOTIFICATION_POLICY_NOT_FOUND", status_code=404)
    if policy.builtin_key:
        raise ServiceError("Built-in policies cannot be deleted", code="NOTIFICATION_POLICY_BUILTIN")
    session.delete(policy)
    session.flush()


def _policy_matches(session: Session, policy: NotificationPolicy, payload: dict[str, Any]) -> bool:
    platforms = {str(item).lower() for item in _json_list(policy.platforms_json)}
    failures = {str(item).upper() for item in _json_list(policy.failure_types_json)}
    groups = _clean_ints(_json_list(policy.group_ids_json))
    if platforms and str(payload.get("platform") or "").lower() not in platforms:
        return False
    if failures and str(payload.get("failure_type") or "UNKNOWN").upper() not in failures:
        return False
    if groups and 0 not in groups:
        group_id = int(payload.get("group_id") or 0)
        allowed: set[int] = set(groups)
        if policy.include_descendants:
            allowed.update(crud.expand_group_ids(session, [item for item in groups if item > 0], include_special_ids=False))
        if group_id == 0:
            return -1 in allowed or 0 in allowed
        if group_id not in allowed:
            return False
    return True


def _render_detailed_batch_fallback(payload: dict[str, Any]) -> tuple[str, str]:
    locale = normalize_locale(str(payload.get("_locale") or "zh-CN"))
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    failed = [item for item in items if not item.get("success") and not item.get("cancelled")]
    cancelled = [item for item in items if item.get("cancelled")]
    changed = [item for item in items if item.get("changed")]
    subject_key = "email.batch.subject.failed" if failed else (
        "email.batch.subject.cancelled" if cancelled else (
            "email.batch.subject.changed" if changed else "email.batch.subject.succeeded"
        )
    )
    subject = translate(locale, subject_key, {
        "failed": len(failed), "cancelled": len(cancelled), "changed": len(changed),
    })
    body = render_email_template(
        "backup_batch_summary.html",
        locale=locale,
        context={
            "run": {
                "total_devices": len(items),
                "success_count": sum(1 for item in items if item.get("success")),
                "fail_count": len(failed),
            },
            "task_time": payload.get("task_time") or "-",
            "failed_records": [
                {
                    "device": {"name": item.get("device_name") or "-", "host": item.get("device_host") or "-"},
                    "record": {"failure_type": item.get("failure_type") or "UNKNOWN"},
                    "duration": item.get("duration") or "-",
                    "error_message": item.get("localized_error_message") or item.get("error_message") or translate(locale, "email.unknown_error"),
                }
                for item in failed
            ],
            "cancelled_records": [
                (
                    {"name": item.get("device_name") or "-", "host": item.get("device_host") or "-"},
                    {
                        "error_message": item.get("localized_error_message") or item.get("error_message") or "",
                        "failure_type": item.get("failure_type") or "CANCELLED",
                    },
                )
                for item in cancelled
            ],
            "changed_records": [
                (
                    {"name": item.get("device_name") or "-", "host": item.get("device_host") or "-"},
                    {},
                    item.get("change_summary_html") or "",
                )
                for item in changed
            ],
        },
    )
    return subject, body


def _payload_with_items(payload: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(payload)
    result["items"] = items
    result["total_count"] = len(items)
    result["failed_count"] = sum(1 for item in items if not item.get("success") and not item.get("cancelled"))
    result["cancelled_count"] = sum(1 for item in items if item.get("cancelled"))
    result["changed_count"] = sum(1 for item in items if item.get("changed"))
    result["success_count"] = sum(1 for item in items if item.get("success"))
    return result


def _batch_matching_event_types(payload: dict[str, Any]) -> set[str]:
    event_types = {"backup_summary"}
    if int(payload.get("failed_count") or 0):
        event_types.add("backup_failed")
    if int(payload.get("cancelled_count") or 0):
        event_types.add("task_cancelled")
    if int(payload.get("changed_count") or 0):
        event_types.add("config_changed")
    return event_types


def _batch_item_matches_events(item: dict[str, Any], event_types: set[str]) -> bool:
    return bool(
        ("backup_failed" in event_types and not item.get("success") and not item.get("cancelled"))
        or ("task_cancelled" in event_types and item.get("cancelled"))
        or ("config_changed" in event_types and item.get("changed"))
    )


def _subset_payload(session: Session, policy: NotificationPolicy, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = payload.get("items")
    if not isinstance(items, list):
        return payload if _policy_matches(session, policy, payload) else None
    matched = [item for item in items if isinstance(item, dict) and _policy_matches(session, policy, item)]
    if not matched:
        return None
    result = _payload_with_items(payload, matched)
    if len(matched) != len(items):
        result["fallback_subject"], result["fallback_body"] = _render_detailed_batch_fallback(result)
        result["_is_subset"] = True
    return result


def _template_matches_channel(template: NotificationTemplate, channel_type: str) -> bool:
    return (
        template.channel_type in {"*", channel_type}
        or template.channel_type == "robot" and channel_type in ROBOT_CHANNEL_TYPES
    )


def _resolve_template_for_channel(
    session: Session,
    selected_template: NotificationTemplate | None,
    channel_type: str,
) -> NotificationTemplate | None:
    if selected_template is None:
        return None
    if selected_template.builtin_key not in BUILTIN_BACKUP_TEMPLATE_KEYS:
        return selected_template if selected_template.enabled else None
    builtin_key = BUILTIN_TEMPLATE_CHANNEL_KEYS.get(channel_type)
    if not builtin_key:
        return None
    resolved = session.exec(
        select(NotificationTemplate).where(NotificationTemplate.builtin_key == builtin_key)
    ).first()
    return resolved if resolved and resolved.enabled else None


def _report_labels(locale: str) -> dict[str, str]:
    keys = (
        "title", "task_time", "result", "total", "unit", "succeeded", "failed", "cancelled",
        "changed",
        "failed_section", "cancelled_section", "changed_section", "device_name", "device_host",
        "duration", "failure_type", "error", "details", "change_summary", "changed_detail",
    )
    labels = {
        key: translate(locale, f"notification.template.report.{key}")
        for key in keys
    }
    labels["diff_rules_applied"] = translate(locale, "email.diff_rules_applied")
    return labels


def _feishu_cell(value: Any, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "-")).strip() or "-"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _feishu_table_context(payload: dict[str, Any]) -> dict[str, Any]:
    locale = normalize_locale(str(payload.get("_locale") or payload.get("locale") or "zh-CN"))
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else _report_labels(locale)
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    failed = [item for item in items if not item.get("success") and not item.get("cancelled")]
    cancelled = [item for item in items if item.get("cancelled")]
    changed = [item for item in items if item.get("changed")]

    def compact(source: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "device_name": _feishu_cell(item.get("device_name")),
                "device_host": _feishu_cell(item.get("device_host")),
                "duration": _feishu_cell(item.get("duration"), limit=64),
                "failure_type": _feishu_cell(item.get("failure_type"), limit=64),
            }
            for item in source[:FEISHU_TABLE_ROW_LIMIT]
        ]

    def hidden_label(source: list[dict[str, Any]]) -> str:
        hidden_count = max(0, len(source) - FEISHU_TABLE_ROW_LIMIT)
        return (
            translate(locale, "notification.template.feishu_hidden_notice", {"count": hidden_count})
            if hidden_count else ""
        )

    unit = str(labels.get("unit") or "")
    result = (
        f"{labels.get('total')} {int(payload.get('total_count') or len(items))} {unit} · "
        f"{labels.get('succeeded')} {int(payload.get('success_count') or 0)} {unit} · "
        f"{labels.get('failed')} {int(payload.get('failed_count') or len(failed))} {unit} · "
        f"{labels.get('cancelled')} {int(payload.get('cancelled_count') or len(cancelled))} {unit} · "
        f"{labels.get('changed')} {int(payload.get('changed_count') or len(changed))} {unit}"
    )
    failed_items = compact(failed)
    cancelled_items = compact(cancelled)
    changed_items = compact(changed)
    return {
        "feishu_header_template": "red" if failed else "orange" if cancelled or changed else "green",
        "feishu_summary_text": (
            f"**{labels.get('task_time')}**: {_feishu_cell(payload.get('task_time'), limit=255)}\n"
            f"**{labels.get('result')}**: {result}"
        ),
        "feishu_failed_title": f"**{labels.get('failed_section')} ({len(failed)} {unit})**",
        "feishu_cancelled_title": f"**{labels.get('cancelled_section')} ({len(cancelled)} {unit})**",
        "feishu_changed_title": f"**{labels.get('changed_section')} ({len(changed)} {unit})**",
        "feishu_failed_items": failed_items,
        "feishu_cancelled_items": cancelled_items,
        "feishu_changed_items": changed_items,
        "feishu_failed_rows_json": _dump(failed_items),
        "feishu_cancelled_rows_json": _dump([
            {"device_name": item["device_name"], "device_host": item["device_host"]}
            for item in cancelled_items
        ]),
        "feishu_changed_rows_json": _dump([
            {"device_name": item["device_name"], "device_host": item["device_host"]}
            for item in changed_items
        ]),
        "feishu_failed_hidden_label": hidden_label(failed),
        "feishu_cancelled_hidden_label": hidden_label(cancelled),
        "feishu_changed_hidden_label": hidden_label(changed),
    }


def normalize_backup_payload(payload: dict[str, Any], locale: str, fallback_subject: str = "") -> dict[str, Any]:
    result = dict(payload)
    source_items = result.get("items")
    if isinstance(source_items, list):
        raw_items = [dict(item) for item in source_items if isinstance(item, dict)]
    elif any(key in result for key in ("device_id", "device_name", "device_host")):
        raw_items = [{
            key: result.get(key)
            for key in (
                "device_id", "device_name", "device_host", "group_id", "platform", "failure_type",
                "error_message", "localized_error_message", "duration", "finished_at", "success",
                "cancelled", "changed", "change_lines",
                "change_context_lines", "change_total_rows", "change_sample_limit",
            )
        }]
    else:
        raw_items = []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        cancelled = bool(raw.get("cancelled"))
        success = bool(raw.get("success"))
        changed = bool(raw.get("changed"))
        change_lines = [
            {
                "prefix": str(line.get("prefix") or ""),
                "text": str(line.get("text") or ""),
                "kind": str(line.get("kind") or "context"),
            }
            for line in (raw.get("change_lines") or [])
            if isinstance(line, dict)
        ]
        context_lines = int(raw.get("change_context_lines") or 0)
        total_rows = int(raw.get("change_total_rows") or len(change_lines))
        sample_limit = int(raw.get("change_sample_limit") or len(change_lines))
        items.append({
            "device_id": raw.get("device_id"),
            "device_name": str(raw.get("device_name") or "-"),
            "device_host": str(raw.get("device_host") or "-"),
            "group_id": raw.get("group_id"),
            "platform": str(raw.get("platform") or ""),
            "failure_type": str(raw.get("failure_type") or ("CANCELLED" if cancelled else "UNKNOWN" if not success else "-")),
            "error_message": str(raw.get("localized_error_message") or raw.get("error_message") or "-"),
            "duration": str(raw.get("duration") or "-"),
            "finished_at": str(raw.get("finished_at") or "-"),
            "success": success,
            "cancelled": cancelled,
            "changed": changed,
            "change_lines": change_lines,
            "change_context_label": (
                translate(locale, "email.changed_lines_context", {"context": context_lines})
                if change_lines else ""
            ),
            "change_truncated_label": (
                translate(locale, "email.changed_lines_truncated", {"count": len(change_lines)})
                if total_rows > sample_limit else ""
            ),
        })
    failed_count = sum(1 for item in items if not item["success"] and not item["cancelled"])
    cancelled_count = sum(1 for item in items if item["cancelled"])
    changed_count = sum(1 for item in items if item["changed"])
    success_count = sum(1 for item in items if item["success"])
    subject_key = "email.batch.subject.failed" if failed_count else (
        "email.batch.subject.cancelled" if cancelled_count else (
            "email.batch.subject.changed" if changed_count else "email.batch.subject.succeeded"
        )
    )
    result.update({
        "locale": normalize_locale(locale),
        "items": items,
        "total_count": len(items),
        "success_count": success_count,
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "changed_count": changed_count,
        "task_time": str(result.get("task_time") or (items[0].get("finished_at") if items else "-") or "-"),
        "labels": _report_labels(normalize_locale(locale)),
        "summary_subject": translate(
            normalize_locale(locale),
            subject_key,
            {"failed": failed_count, "cancelled": cancelled_count, "changed": changed_count},
            fallback=fallback_subject or "EasyNetBak backup report",
        ),
    })
    return result


def dispatch_event(
    session: Session,
    *,
    event_type: str,
    source_key: str,
    locale: str,
    payload: dict[str, Any],
    fallback_subject: str,
    fallback_body: str,
    email_sender=send_email,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ServiceError("Unsupported notification event", code="NOTIFICATION_EVENT_INVALID")
    ensure_builtin_defaults(session)
    existing = session.exec(select(NotificationEvent).where(NotificationEvent.source_key == source_key)).first()
    if existing:
        deliveries = list(session.exec(select(NotificationDelivery).where(NotificationDelivery.event_id == existing.id)))
        return _dispatch_result(deliveries, duplicate=True)
    stored_payload = normalize_backup_payload(payload, locale, fallback_subject)
    stored_payload["_locale"] = locale
    stored_payload["fallback_subject"] = fallback_subject
    stored_payload["fallback_body"] = fallback_body
    event = NotificationEvent(
        event_type=event_type,
        source_key=source_key[:255],
        locale=locale,
        payload_json=_dump(stored_payload),
    )
    session.add(event)
    session.flush()

    deliveries: list[NotificationDelivery] = []
    matching_event_types = (
        _batch_matching_event_types(stored_payload)
        if event_type == "backup_summary"
        else {event_type}
    )
    policies = [
        item
        for item in list_policies(session)
        if item.enabled and matching_event_types.intersection(_json_list(item.event_types_json))
    ]
    channels = {item.id: item for item in session.exec(select(NotificationChannel).where(NotificationChannel.enabled == True))}  # noqa: E712
    original_items = stored_payload.get("items") if isinstance(stored_payload.get("items"), list) else None
    stopped_item_ids: set[int] = set()
    for policy in policies:
        policy_event_types = set(_json_list(policy.event_types_json))
        selected_template = None
        if policy.template_id:
            selected_template = session.get(NotificationTemplate, policy.template_id)
            if selected_template is None:
                continue
        candidate_payload = stored_payload
        if event_type == "backup_summary" and original_items is not None and "backup_summary" not in policy_event_types:
            event_items = [
                item
                for item in original_items
                if isinstance(item, dict) and _batch_item_matches_events(item, policy_event_types)
            ]
            if not event_items:
                continue
            candidate_payload = _payload_with_items(stored_payload, event_items)
            if len(event_items) != len(original_items):
                candidate_payload["fallback_subject"], candidate_payload["fallback_body"] = (
                    _render_detailed_batch_fallback(candidate_payload)
                )
                candidate_payload["_is_subset"] = True
        if original_items is not None and stopped_item_ids:
            candidate_items = [
                item
                for item in candidate_payload.get("items", [])
                if id(item) not in stopped_item_ids
            ]
            if not candidate_items:
                continue
            candidate_payload = _payload_with_items(candidate_payload, candidate_items)
        routed_payload = _subset_payload(session, policy, candidate_payload)
        if routed_payload is None:
            continue
        for channel_id in _clean_ints(_json_list(policy.channel_ids_json)):
            if channel_id not in channels:
                continue
            resolved_template = _resolve_template_for_channel(
                session,
                selected_template,
                channels[channel_id].channel_type,
            )
            if selected_template is not None and resolved_template is None:
                continue
            dedupe = hashlib.sha256(f"{event.id}:{policy.id}:{channel_id}".encode()).hexdigest()
            delivery = NotificationDelivery(
                event_id=event.id,
                policy_id=policy.id,
                channel_id=channel_id,
                template_id=resolved_template.id if resolved_template else None,
                payload_json=_dump(routed_payload),
                dedupe_key=dedupe,
            )
            session.add(delivery)
            session.flush()
            deliver_notification(session, delivery, email_sender=email_sender)
            deliveries.append(delivery)
        if policy.stop_processing and original_items is not None:
            stopped_item_ids.update(id(item) for item in routed_payload.get("items", []))
        elif policy.stop_processing:
            break
    return _dispatch_result(deliveries)


def _dispatch_result(deliveries: list[NotificationDelivery], *, duplicate: bool = False) -> dict[str, Any]:
    attempted = len(deliveries)
    sent = sum(1 for item in deliveries if item.status == "sent")
    failed = sum(1 for item in deliveries if item.status in {"failed", "retrying"})
    return {
        "attempted": attempted,
        "sent": sent,
        "failed": failed,
        "duplicate": duplicate,
        "delivery_ids": [item.id for item in deliveries if item.id],
    }


def _strip_html(value: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", value or "", flags=re.I)
    text = re.sub(r"</(?:p|div|tr|h\d)>", "\n", text, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _render_delivery(session: Session, delivery: NotificationDelivery, channel: NotificationChannel) -> tuple[str, str, str]:
    event = session.get(NotificationEvent, delivery.event_id)
    if event is None:
        raise ServiceError("Notification event not found", code="NOTIFICATION_EVENT_NOT_FOUND")
    payload = _json_dict(delivery.payload_json) or _json_dict(event.payload_json)
    context = {"event": {"id": str(event.id), "type": event.event_type, "created_at": event.created_at.isoformat()}, **payload}
    template = session.get(NotificationTemplate, delivery.template_id) if delivery.template_id else None
    if template:
        if not template.enabled:
            raise ServiceError("Notification template is disabled", code="NOTIFICATION_TEMPLATE_DISABLED")
        if not _template_matches_channel(template, channel.channel_type):
            raise ServiceError("Template does not match channel", code="NOTIFICATION_TEMPLATE_MISMATCH")
        if template.builtin_key == BUILTIN_FEISHU_TEMPLATE_KEY:
            context.update(_feishu_table_context(context))
        subject = render_custom_template(template.subject_template or payload.get("fallback_subject", ""), context, content_type="text")
        body = render_custom_template(template.body_template, context, content_type=template.content_type)
        return subject.replace("\r", " ").replace("\n", " ")[:255], body, template.content_type
    subject = str(payload.get("fallback_subject") or "EasyNetBak notification").replace("\r", " ").replace("\n", " ")[:255]
    body = str(payload.get("fallback_body") or "")
    if channel.channel_type != "smtp":
        body = _strip_html(body)
    return subject, body, "html" if channel.channel_type == "smtp" else "markdown"


def _channel_secrets(channel: NotificationChannel) -> dict[str, Any]:
    raw = decrypt_secret(channel.secret_encrypted)
    parsed = _json_dict(raw)
    return parsed if parsed else ({"password": raw} if raw else {})


def deliver_notification(session: Session, delivery: NotificationDelivery, *, email_sender=send_email) -> bool:
    if delivery.status == "sent":
        return True
    channel = session.get(NotificationChannel, delivery.channel_id)
    if channel is None or not channel.enabled:
        delivery.status = "failed"
        delivery.last_error = "Channel is missing or disabled"
        delivery.updated_at = datetime.utcnow()
        session.add(delivery)
        session.flush()
        return False
    if delivery.template_id:
        template = session.get(NotificationTemplate, delivery.template_id)
        if template is None or not template.enabled:
            delivery.status = "failed"
            delivery.last_error = "Template is missing or disabled"
            delivery.updated_at = datetime.utcnow()
            session.add(delivery)
            session.flush()
            return False
    delivery.status = "sending"
    delivery.attempts += 1
    delivery.updated_at = datetime.utcnow()
    session.add(delivery)
    session.flush()
    try:
        subject, body, content_type = _render_delivery(session, delivery, channel)
        delivery.subject = subject
        session.add(delivery)
        session.flush()
        _send_channel(
            channel,
            subject=subject,
            body=body,
            content_type=content_type,
            payload=_json_dict(delivery.payload_json),
            email_sender=email_sender,
        )
        delivery.status = "sent"
        delivery.sent_at = datetime.utcnow()
        delivery.next_attempt_at = None
        delivery.last_error = None
        success = True
    except Exception as exc:
        delivery.last_error = _sanitize_error(exc)
        if delivery.attempts < MAX_ATTEMPTS:
            delivery.status = "retrying"
            delivery.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * (2 ** (delivery.attempts - 1))))
            _queue_retry(delivery.id)
        else:
            delivery.status = "failed"
            delivery.next_attempt_at = None
        logger.warning("Notification delivery failed channel_id=%s delivery_id=%s error=%s", channel.id, delivery.id, delivery.last_error)
        success = False
    delivery.updated_at = datetime.utcnow()
    session.add(delivery)
    session.flush()
    return success


def _queue_retry(delivery_id: int | None) -> None:
    if not delivery_id or not (settings.celery.broker_url or "").strip():
        return
    try:
        from app.celery_app import celery_app

        celery_app.send_task("app.deliver_notification", args=[int(delivery_id)], countdown=5)
    except Exception as exc:
        logger.warning("Could not queue notification retry delivery_id=%s error=%s", delivery_id, _sanitize_error(exc))


def _send_channel(
    channel: NotificationChannel,
    *,
    subject: str,
    body: str,
    content_type: str,
    payload: dict[str, Any],
    email_sender=send_email,
) -> None:
    config = _json_dict(channel.config_json)
    secrets = _channel_secrets(channel)
    if channel.channel_type == "smtp":
        smtp_config = {
            "smtp_host": config.get("host"),
            "smtp_port": str(config.get("port") or "25"),
            "smtp_user": config.get("user"),
            "smtp_pass": secrets.get("password"),
            "smtp_from": config.get("from"),
            "smtp_to": config.get("to"),
        }
        result = email_sender(
            subject,
            body,
            content_type="html" if content_type == "html" else "plain",
            smtp_config=smtp_config,
        )
        if result is False:
            raise RuntimeError("SMTP configuration is incomplete")
        return
    url = str(secrets.get("url") or "").strip()
    if not url:
        raise RuntimeError("Webhook URL is not configured")
    if channel.channel_type == "dingtalk" and secrets.get("signing_secret"):
        timestamp = str(int(time.time() * 1000))
        signature = base64.b64encode(
            hmac.new(str(secrets["signing_secret"]).encode(), f"{timestamp}\n{secrets['signing_secret']}".encode(), hashlib.sha256).digest()
        ).decode()
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({"timestamp": timestamp, "sign": signature})
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    if channel.channel_type == "wecom":
        data = {"msgtype": "markdown", "markdown": {"content": f"**{subject}**\n{body}"}}
    elif channel.channel_type == "dingtalk":
        data = {"msgtype": "markdown", "markdown": {"title": subject, "text": f"### {subject}\n{body}"}}
    elif channel.channel_type == "feishu":
        if content_type == "json":
            card = json.loads(body)
            if not isinstance(card, dict) or card.get("schema") != "2.0":
                raise RuntimeError("Feishu JSON template must render a JSON 2.0 card object")
            header = card.setdefault("header", {})
            if not isinstance(header, dict):
                raise RuntimeError("Feishu JSON template header must be an object")
            header["title"] = {"tag": "plain_text", "content": subject}
            data = {"msg_type": "interactive", "card": card}
        else:
            data = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "blue",
                        "title": {"tag": "plain_text", "content": subject},
                    },
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                    ],
                },
            }
        if secrets.get("signing_secret"):
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{secrets['signing_secret']}"
            data.update({"timestamp": timestamp, "sign": base64.b64encode(hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()).decode()})
        if len(_dump(data).encode("utf-8")) > FEISHU_CARD_MAX_BYTES:
            raise RuntimeError("Feishu interactive card exceeds the 30 KB payload limit")
    elif channel.channel_type == "webhook" and content_type == "json":
        parsed_body = json.loads(body)
        if not isinstance(parsed_body, dict):
            raise RuntimeError("Webhook JSON template must render an object")
        data = parsed_body
    else:
        data = {"event": payload.get("event_type") or "notification", "title": subject, "content": body, "data": payload}
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "EasyNetBak-Notifier/1.0"}
    if secrets.get("authorization"):
        headers["Authorization"] = str(secrets["authorization"])
    _post_json(url, data, timeout=max(1, min(int(config.get("timeout") or 10), 30)), allow_private=bool(config.get("allow_private")), headers=headers)


def _post_json(url: str, data: dict[str, Any], *, timeout: int, allow_private: bool, headers: dict[str, str]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("Webhook URL must be an HTTP(S) URL without embedded credentials")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise RuntimeError("Webhook hostname could not be resolved") from exc
    if not allow_private:
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise RuntimeError("Webhook target is not a public address")
    request = Request(url, data=_dump(data).encode("utf-8"), headers=headers, method="POST")
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(4096)
            if not 200 <= int(response.status) < 300:
                raise RuntimeError(f"Webhook returned HTTP {response.status}")
    except HTTPError as exc:
        raise RuntimeError(f"Webhook returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("Webhook connection failed") from exc


def retry_delivery(session: Session, delivery_id: int) -> bool:
    delivery = session.get(NotificationDelivery, delivery_id)
    if delivery is None:
        return False
    if delivery.status == "sent" or delivery.attempts >= MAX_ATTEMPTS:
        return delivery.status == "sent"
    return deliver_notification(session, delivery)


def sample_template_context(*, locale: str = "zh-CN", event_type: str = "*") -> dict[str, Any]:
    event_type = event_type if event_type in EVENT_TYPES else "*"
    items = [
        {
            "device_id": 42, "device_name": "core-switch-01", "device_host": "192.0.2.10", "platform": "cisco_ios",
            "group_id": 1, "failure_type": "TIMEOUT", "localized_error_message": "Connection timed out",
            "duration": "10.25s", "finished_at": "2026-07-17 10:00:00",
            "success": False, "cancelled": False, "changed": False,
        },
        {
            "device_id": 43, "device_name": "edge-switch-02", "device_host": "192.0.2.11", "platform": "huawei",
            "group_id": 2, "failure_type": "CANCELLED", "localized_error_message": "Task cancelled",
            "duration": "-", "finished_at": "2026-07-17 10:00:00",
            "success": False, "cancelled": True, "changed": False,
        },
        {
            "device_id": 44, "device_name": "access-switch-03", "device_host": "192.0.2.12", "platform": "cisco_ios",
            "group_id": 1, "failure_type": "-", "error_message": "-", "duration": "3.18s",
            "finished_at": "2026-07-17 10:00:00", "success": True, "cancelled": False, "changed": True,
            "change_context_lines": 3,
            "change_lines": [
                {"prefix": "+", "text": "logging host 192.0.2.200", "kind": "add"},
                {"prefix": "-", "text": "logging host 192.0.2.100", "kind": "del"},
            ],
        },
    ]
    selected_items = {
        "backup_failed": items[:1],
        "task_cancelled": items[1:2],
        "config_changed": items[2:],
    }.get(event_type, items)
    context = normalize_backup_payload(
        {"task_time": "2026-07-17 10:00:00", "items": selected_items},
        locale,
    )
    context.update(_feishu_table_context(context))
    context["event"] = {
        "id": "sample",
        "type": "backup_summary" if event_type == "*" else event_type,
        "created_at": "2026-07-17T10:00:00",
    }
    if event_type != "backup_summary":
        item = selected_items[0]
        context.update({
            "device_id": item["device_id"],
            "device_name": item["device_name"],
            "device_host": item["device_host"],
            "platform": item["platform"],
            "group_id": item["group_id"],
            "failure_type": item["failure_type"],
            "error_message": item.get("localized_error_message") or item.get("error_message") or "-",
        })
    return context


def template_variable_catalog(*, locale: str = "zh-CN") -> list[dict[str, Any]]:
    locale = normalize_locale(locale)

    def variable(path: str, type_name: str, description_key: str, example: Any, insert: str, **extra: Any) -> dict[str, Any]:
        return {
            "path": path,
            "type": type_name,
            "description": translate(locale, description_key),
            "example": example,
            "insert": insert,
            **extra,
        }

    labels = _report_labels(locale)
    label_variables = [
        {
            "path": f'labels["{key}"]',
            "type": "string",
            "description": translate(locale, "notification.template.variable.label_item", {"label": value}),
            "example": value,
            "insert": f'{{{{ labels["{key}"] }}}}',
            "parent": "labels",
        }
        for key, value in labels.items()
    ]

    groups = [
        {
            "key": "summary",
            "label": translate(locale, "notification.template.variable_group.summary"),
            "variables": [
                variable("summary_subject", "string", "notification.template.variable.summary_subject", "【备份汇总报告】发现 1 台设备备份失败", "{{ summary_subject }}"),
                variable("locale", "string", "notification.template.variable.locale", locale, "{{ locale }}"),
                variable("task_time", "string", "notification.template.variable.task_time", "2026-07-17 10:00:00", "{{ task_time }}"),
                variable("total_count", "number", "notification.template.variable.total_count", 3, "{{ total_count }}"),
                variable("success_count", "number", "notification.template.variable.success_count", 1, "{{ success_count }}"),
                variable("failed_count", "number", "notification.template.variable.failed_count", 1, "{{ failed_count }}"),
                variable("cancelled_count", "number", "notification.template.variable.cancelled_count", 1, "{{ cancelled_count }}"),
                variable("changed_count", "number", "notification.template.variable.changed_count", 1, "{{ changed_count }}"),
            ],
        },
        {
            "key": "items",
            "label": translate(locale, "notification.template.variable_group.items"),
            "variables": [
                variable("items", "list", "notification.template.variable.items", "3 items", "{% for item in items %}\n  {{ item[\"device_name\"] }}\n{% endfor %}"),
                variable("item[\"device_id\"]", "number|null", "notification.template.variable.item_device_id", 42, "{{ item[\"device_id\"]|default(\"-\") }}", parent="items"),
                variable("item[\"device_name\"]", "string", "notification.template.variable.item_device_name", "core-switch-01", "{{ item[\"device_name\"] }}", parent="items"),
                variable("item[\"device_host\"]", "string", "notification.template.variable.item_device_host", "192.0.2.10", "{{ item[\"device_host\"] }}", parent="items"),
                variable("item[\"group_id\"]", "number|null", "notification.template.variable.item_group_id", 1, "{{ item[\"group_id\"]|default(\"-\") }}", parent="items"),
                variable("item[\"platform\"]", "string", "notification.template.variable.item_platform", "cisco_ios", "{{ item[\"platform\"] }}", parent="items"),
                variable("item[\"failure_type\"]", "string", "notification.template.variable.item_failure_type", "TIMEOUT", "{{ item[\"failure_type\"] }}", parent="items"),
                variable("item[\"error_message\"]", "string", "notification.template.variable.item_error_message", "Connection timed out", "{{ item[\"error_message\"] }}", parent="items"),
                variable("item[\"duration\"]", "string", "notification.template.variable.item_duration", "10.25s", "{{ item[\"duration\"] }}", parent="items"),
                variable("item[\"finished_at\"]", "string", "notification.template.variable.item_finished_at", "2026-07-17 10:00:00", "{{ item[\"finished_at\"] }}", parent="items"),
                variable("item[\"success\"]", "boolean", "notification.template.variable.item_success", False, "{{ item[\"success\"] }}", parent="items"),
                variable("item[\"cancelled\"]", "boolean", "notification.template.variable.item_cancelled", False, "{{ item[\"cancelled\"] }}", parent="items"),
                variable("item[\"changed\"]", "boolean", "notification.template.variable.item_changed", True, "{{ item[\"changed\"] }}", parent="items"),
                variable(
                    "item[\"change_lines\"]", "list", "notification.template.variable.item_change_lines", "2 lines",
                    "{% for line in item[\"change_lines\"] %}\n  {{ line[\"prefix\"] }} {{ line[\"text\"] }}\n{% endfor %}", parent="items",
                ),
                variable("line[\"prefix\"]", "string", "notification.template.variable.line_prefix", "+", "{{ line[\"prefix\"] }}", parent="change_lines"),
                variable("line[\"text\"]", "string", "notification.template.variable.line_text", "logging host 192.0.2.200", "{{ line[\"text\"] }}", parent="change_lines"),
                variable("line[\"kind\"]", "string", "notification.template.variable.line_kind", "add", "{{ line[\"kind\"] }}", parent="change_lines"),
                variable("item[\"change_context_label\"]", "string", "notification.template.variable.item_change_context_label", translate(locale, "email.changed_lines_context", {"context": 3}), "{{ item[\"change_context_label\"] }}", parent="items"),
                variable("item[\"change_truncated_label\"]", "string", "notification.template.variable.item_change_truncated_label", "", "{{ item[\"change_truncated_label\"] }}", parent="items"),
            ],
        },
        {
            "key": "event",
            "label": translate(locale, "notification.template.variable_group.event"),
            "variables": [
                variable("event[\"id\"]", "string", "notification.template.variable.event_id", "sample", "{{ event[\"id\"] }}"),
                variable("event[\"type\"]", "string", "notification.template.variable.event_type", "backup_summary", "{{ event[\"type\"] }}"),
                variable("event[\"created_at\"]", "string", "notification.template.variable.event_created_at", "2026-07-17T10:00:00", "{{ event[\"created_at\"] }}"),
                variable("labels", "object", "notification.template.variable.labels", translate(locale, "notification.template.report.title"), "{{ labels[\"title\"] }}"),
                *label_variables,
            ],
        },
        {
            "key": "device",
            "label": translate(locale, "notification.template.variable_group.device"),
            "hint": translate(locale, "notification.template.variable_group.device_hint"),
            "events": ["backup_failed", "config_changed", "task_cancelled"],
            "variables": [
                variable("device_id", "number|null", "notification.template.variable.device_id", 42, "{{ device_id|default(\"-\") }}"),
                variable("device_name", "string", "notification.template.variable.device_name", "core-switch-01", "{{ device_name }}"),
                variable("device_host", "string", "notification.template.variable.device_host", "192.0.2.10", "{{ device_host }}"),
                variable("platform", "string", "notification.template.variable.platform", "cisco_ios", "{{ platform }}"),
                variable("group_id", "number|null", "notification.template.variable.group_id", 1, "{{ group_id|default(\"-\") }}"),
                variable("failure_type", "string", "notification.template.variable.failure_type", "TIMEOUT", "{{ failure_type|default(\"-\") }}"),
                variable("error_message", "string", "notification.template.variable.error_message", "Connection timed out", "{{ error_message|default(\"-\") }}"),
            ],
        },
    ]
    return groups


def preview_template(
    *, subject_template: str, body_template: str, content_type: str, locale: str = "zh-CN", event_type: str = "*",
) -> dict[str, Any]:
    context = sample_template_context(locale=locale, event_type=event_type)
    return {
        "subject": render_custom_template(subject_template, context, content_type="text"),
        "body": render_custom_template(body_template, context, content_type=content_type),
        "context": context,
    }


def serialize_channel(channel: NotificationChannel, *, locale: str = "zh-CN") -> dict[str, Any]:
    config = _json_dict(channel.config_json)
    secrets = _channel_secrets(channel)
    display_name, name_key = _localized_builtin_name("channel", channel.builtin_key, channel.name, locale)
    return {
        "id": channel.id,
        "name": display_name,
        "channel_type": channel.channel_type,
        "enabled": channel.enabled,
        "config": config,
        "has_url": bool(secrets.get("url")),
        "has_password": bool(secrets.get("password")),
        "has_signing_secret": bool(secrets.get("signing_secret")),
        "has_authorization": bool(secrets.get("authorization")),
        "url_mask": "*" * len(str(secrets.get("url") or "")),
        "password_mask": "*" * len(str(secrets.get("password") or "")),
        "signing_secret_mask": "*" * len(str(secrets.get("signing_secret") or "")),
        "authorization_mask": "*" * len(str(secrets.get("authorization") or "")),
        "has_secret": bool(secrets.get("signing_secret") or secrets.get("password") or secrets.get("authorization")),
        "builtin": bool(channel.builtin_key),
        "name_key": name_key,
    }


def serialize_template(template: NotificationTemplate, *, locale: str = "zh-CN") -> dict[str, Any]:
    display_name, name_key = _localized_builtin_name("template", template.builtin_key, template.name, locale)
    return {
        "id": template.id,
        "name": display_name,
        "enabled": template.enabled,
        "event_type": template.event_type,
        "channel_type": template.channel_type,
        "locale": normalize_locale(locale) if template.builtin_key else template.locale,
        "subject_template": template.subject_template,
        "body_template": template.body_template,
        "content_type": template.content_type,
        "builtin": bool(template.builtin_key),
        "builtin_key": template.builtin_key or "",
        "hint_key": BUILTIN_TEMPLATE_HINT_KEYS.get(template.builtin_key or "", ""),
        "renderer_key": template.renderer_key or "",
        "name_key": name_key,
    }


def serialize_policy(policy: NotificationPolicy, *, locale: str = "zh-CN") -> dict[str, Any]:
    display_name, name_key = _localized_builtin_name("policy", policy.builtin_key, policy.name, locale)
    return {
        "id": policy.id,
        "name": display_name,
        "enabled": policy.enabled,
        "priority": policy.priority,
        "event_types": _json_list(policy.event_types_json),
        "group_ids": _clean_ints(_json_list(policy.group_ids_json)),
        "include_descendants": policy.include_descendants,
        "platforms": _json_list(policy.platforms_json),
        "failure_types": _json_list(policy.failure_types_json),
        "channel_ids": _clean_ints(_json_list(policy.channel_ids_json)),
        "template_id": policy.template_id,
        "stop_processing": policy.stop_processing,
        "builtin": bool(policy.builtin_key),
        "name_key": name_key,
    }
