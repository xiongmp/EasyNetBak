from __future__ import annotations

from app.i18n.catalog import get_messages
from app.i18n.validators import default_locale, normalize_locale

FRONTEND_MESSAGE_NAMESPACES = (
    "dialog.",
    "js.",
    "status.",
    "webshell.",
)

COMMON_FRONTEND_NAMESPACES = (
    "dialog.",
    "status.",
    "js.nb_common.",
    "js.shell_controls.",
    "js.ui_feedback.",
)

PAGE_FRONTEND_NAMESPACES = {
    "dashboard": ("js.dashboard.",),
    "devices": ("js.devices_bulk.", "js.devices_groups.", "js.webshell.", "webshell."),
    "groups": ("js.devices_groups.",),
    "schedules": ("js.schedules.", "js.schedule_stats_"),
    "webshell": ("js.webshell.", "webshell."),
    "webshell_records": ("js.webshell.", "webshell."),
}

FRONTEND_MESSAGE_KEYS = frozenset(
    {
        "audit.csv.action",
        "audit.csv.ip_address",
        "audit.resource.device",
        "audit.resource.group",
        "email.field.backup_time",
        "email.field.device_name",
        "label.error",
        "label.test_time",
        "login.csv.status",
        "task.selected_devices",
        "task.selected_devices.one",
        "task.selected_devices.other",
        "task.start_time",
        "template.backups.no_backup_records",
        "template.backups.platform",
        "template.backups.request_failed",
        "template.backups.selected",
        "template.backups.start_backup",
        "template.api_keys.copied",
        "template.api_keys.copy",
        "template.api_keys.copy_failed_in",
        "template.base.backup_details",
        "template.base.batch_log",
        "template.base.cancel_selected",
        "template.base.close",
        "template.base.collapse_menu",
        "template.base.execution_log",
        "template.base.full_screen",
        "template.base.enter_full_screen",
        "template.base.retry_failed_items",
        "template.base.retry_selected",
        "template.base.task_live_log",
        "template.config_search.backup_record_not_found",
        "template.config_search.exit_full_screen",
        "template.config_search.failed_to_load_backup_content",
        "template.config_search.no_configuration_content",
        "template.config_search.this_account_cannot_view_backup_content",
        "template.config_search.view_configuration",
        "template.dashboard.success_rate",
        "template.device_detail.ungrouped",
        "template.import_result.skip",
        "template.login.enter_username",
        "template.login.signing_in",
        "template.notifications.disabled",
        "template.schedule_stats.cancel_pending_tasks",
        "template.schedules.enabled",
        "template.schedules.preview_failed_to_load_please_try_again",
        "template.storage_settings.connection_test_failed",
        "template.storage_settings.connection_test_succeeded",
        "template.storage_settings.testing",
        "template.templates.add_custom_template",
        "template.templates.edit_custom_template",
        "template.webshell.side_by_side",
        "template.webshell.stacked",
    }
)


def is_frontend_message_key(key: str) -> bool:
    return key in FRONTEND_MESSAGE_KEYS or key.startswith(FRONTEND_MESSAGE_NAMESPACES)


def javascript_messages(locale: str | None, *, page: str | None = None) -> dict[str, str]:
    normalized = normalize_locale(locale)
    namespaces = FRONTEND_MESSAGE_NAMESPACES if page is None else (
        *COMMON_FRONTEND_NAMESPACES,
        *PAGE_FRONTEND_NAMESPACES.get(page, ()),
    )

    def include(key: str) -> bool:
        return key in FRONTEND_MESSAGE_KEYS or key.startswith(namespaces)

    messages = {
        key: value
        for key, value in get_messages(default_locale()).items()
        if include(key)
    }
    if normalized != default_locale():
        messages.update(
            {
                key: value
                for key, value in get_messages(normalized).items()
                if include(key)
            }
        )
    return messages
