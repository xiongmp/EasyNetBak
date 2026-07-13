from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app import crud
from app.core.settings import settings
from app.core.time import format_local_datetime
from app.platforms import PLATFORMS, TELNET_PLATFORMS, TELNET_PLATFORM_IDS, normalize_platform_id
from app.routers.support import _current_user, has_permission, _user_effective_perms
from app.i18n import get_messages, translate
from app.i18n.render import javascript_messages, legacy_javascript_messages
from app.i18n.validators import supported_locales
from app.i18n.legacy import LegacyI18nExtension, legacy_translate_for_template, translate_legacy_text


templates = Jinja2Templates(directory="app/templates")
templates.env.add_extension(LegacyI18nExtension)
templates.env.globals["__legacy"] = legacy_translate_for_template


def _dt_local_str(value: datetime | None, *, offset_minutes: int) -> str:
    return format_local_datetime(value, offset_minutes=offset_minutes)


@pass_context
def _dt_local_filter(ctx, value: datetime | None) -> str:
    request = ctx.get("request")
    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0)) if request else 0
    return _dt_local_str(value, offset_minutes=offset_minutes)


templates.env.filters["dt_local"] = _dt_local_filter
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = settings.app_version


def _layout_context(*, request: Request, active: str) -> dict[str, Any]:
    user = _current_user(request)
    role = getattr(user, "role", "") if user else ""
    eff = _user_effective_perms(user)
    locale = getattr(request.state, "locale", settings.default_locale)
    page_subtitle_keys = {
        "dashboard": "page.dashboard.subtitle",
        "devices": "page.devices.subtitle",
        "groups": "page.groups.subtitle",
        "credentials": "page.credentials.subtitle",
        "templates": "page.templates.subtitle",
        "backups": "page.backups.subtitle",
        "diff_rules": "page.diff_rules.subtitle",
        "config_search": "page.config_search.subtitle",
        "schedules": "page.schedules.subtitle",
        "audit_logs": "page.audit_logs.subtitle",
        "login_logs": "page.login_logs.subtitle",
        "webshell_records": "page.webshell_records.subtitle",
        "settings": "page.settings.subtitle",
        "storage_settings": "page.storage_settings.subtitle",
        "api_keys": "page.api_keys.subtitle",
        "notifications": "page.notifications.subtitle",
        "users": "page.users.subtitle",
        "roles": "page.roles.subtitle",
        "profile": "page.profile.subtitle",
    }
    return {
        "request": request,
        "active": active,
        "page_subtitle_key": page_subtitle_keys.get(active),
        "platforms": PLATFORMS,
        "ssh_platforms": PLATFORMS,
        "telnet_platforms": TELNET_PLATFORMS,
        "telnet_platform_ids": TELNET_PLATFORM_IDS,
        "telnet_platform_base_ids": [normalize_platform_id(pid) for pid in TELNET_PLATFORM_IDS],
        "current_user": user,
        "is_admin": crud.is_admin_role_code(role),
        "is_operator": role in ("admin", "operator"),
        "perms": eff if eff is not None else {"*"},
        "has_permission": lambda code: has_permission(user, code),
        "role_labels": {
            code: translate_legacy_text(label, locale)
            for code, label in getattr(crud, "ROLE_LABELS", {}).items()
        },
        "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
        "locale": locale,
        "supported_locales": supported_locales(),
        "locale_label_map": {
            locale_code: get_messages(locale_code).get("language.name", locale_code)
            for locale_code in supported_locales()
        },
        "js_messages": javascript_messages(locale),
        "legacy_js_messages": legacy_javascript_messages(locale),
        "_": lambda key, params=None, fallback=None: translate(locale, key, params, fallback),
    }
