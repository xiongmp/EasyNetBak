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
from app.i18n import get_messages, has_key, message_placeholders, translate, translate_plural
from app.i18n.render import javascript_messages
from app.i18n.validators import locale_capabilities, supported_locales


templates = Jinja2Templates(directory="app/templates")


@pass_context
def _template_translate(ctx, key: str, params=None, fallback=None) -> str:
    return translate(ctx.get("locale", settings.default_locale), key, params, fallback)


templates.env.globals["_"] = _template_translate


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
    flash_params = dict(request.query_params)
    flash_message_key = (request.query_params.get("msg") or "").strip()
    flash_error_key = (request.query_params.get("err") or "").strip()
    def localized_flash(key: str) -> str:
        if not key or not has_key(key, locale):
            return ""
        selected_params = {
            name: flash_params[name]
            for name in message_placeholders(locale, key)
            if name in flash_params
        }
        if "count" in flash_params and has_key(f"{key}.other", locale):
            try:
                count = int(flash_params["count"])
            except (TypeError, ValueError):
                pass
            else:
                plural_params = {
                    name: flash_params[name]
                    for name in message_placeholders(locale, f"{key}.other")
                    if name in flash_params
                }
                return translate_plural(locale, key, count, plural_params)
        return translate(locale, key, selected_params)
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
            code: translate(locale, f"role.{code}", fallback=label) if code in {"admin", "operator", "readonly"} else label
            for code, label in getattr(crud, "ROLE_LABELS", {}).items()
        },
        "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
        "locale": locale,
        "locale_capabilities": locale_capabilities(locale).as_dict(),
        "supported_locales": supported_locales(),
        "locale_label_map": {
            locale_code: get_messages(locale_code).get("language.name", locale_code)
            for locale_code in supported_locales()
        },
        "js_messages": javascript_messages(locale, page=active),
        "flash_message": localized_flash(flash_message_key),
        "flash_error": localized_flash(flash_error_key),
        "_": lambda key, params=None, fallback=None: translate(locale, key, params, fallback),
    }
