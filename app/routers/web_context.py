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


templates = Jinja2Templates(directory="app/templates")


def _dt_local_str(value: datetime | None, *, offset_minutes: int) -> str:
    return format_local_datetime(value, offset_minutes=offset_minutes)


@pass_context
def _dt_local_filter(ctx, value: datetime | None) -> str:
    request = ctx.get("request")
    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0)) if request else 0
    return _dt_local_str(value, offset_minutes=offset_minutes)


templates.env.filters["dt_local"] = _dt_local_filter
templates.env.globals["app_version"] = settings.app_version


def _layout_context(*, request: Request, active: str) -> dict[str, Any]:
    user = _current_user(request)
    role = getattr(user, "role", "") if user else ""
    eff = _user_effective_perms(user)
    return {
        "request": request,
        "active": active,
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
        "role_labels": getattr(crud, "ROLE_LABELS", {}),
        "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
    }
