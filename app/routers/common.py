from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlmodel import Session

from app import crud
from app.platforms import PLATFORMS, TELNET_PLATFORMS, TELNET_PLATFORM_IDS, normalize_platform_id


templates = Jinja2Templates(directory="app/templates")


def _dt_local_str(value: datetime | None, *, offset_minutes: int) -> str:
    if value is None:
        return ""
    return (value + timedelta(minutes=int(offset_minutes))).strftime("%Y-%m-%d %H:%M:%S")


@pass_context
def _dt_local_filter(ctx, value: datetime | None) -> str:
    request = ctx.get("request")
    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0)) if request else 0
    return _dt_local_str(value, offset_minutes=offset_minutes)


templates.env.filters["dt_local"] = _dt_local_filter


def _current_user(request: Request):
    return getattr(request.state, "user", None)


def _require_admin(request: Request):
    user = _current_user(request)
    if not user or getattr(user, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _require_operator(request: Request):
    user = _current_user(request)
    if not user or getattr(user, "role", "") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Operator or Admin only")
    return user


def get_user_allowed_group_ids(user) -> list[int] | None:
    """
    Returns a list of allowed group IDs for the user.
    Returns None if the user has full access (Admin or access_type='all').
    Returns empty list if user has no access.
    """
    if not user:
        return []
    
    # Admin always has full access
    if getattr(user, "role", "") == "admin":
        return None
        
    # Check access type
    if getattr(user, "group_access_type", "all") == "all":
        return None
        
    # Parse allowed IDs
    raw_ids = getattr(user, "allowed_group_ids", "") or ""
    ids = []
    for x in raw_ids.split(","):
        x = x.strip()
        # Supports positive integers and -1 (for ungrouped)
        if x.lstrip("-").isdigit(): 
            ids.append(int(x))
    return ids



def _log_action(
    request: Request,
    session: Session,
    action: str,
    resource_type: str,
    resource_id: str | int | None = None,
    details: str | None = None,
):
    user = _current_user(request)
    crud.create_audit_log(
        session,
        user_id=int(user.id) if user and user.id else None,
        username=user.username if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        details=details,
        ip_address=request.client.host if request.client else None,
    )


def _layout_context(*, request: Request, active: str) -> dict[str, Any]:
    user = _current_user(request)
    role = getattr(user, "role", "") if user else ""
    return {
        "request": request,
        "active": active,
        "platforms": PLATFORMS,
        "ssh_platforms": PLATFORMS,
        "telnet_platforms": TELNET_PLATFORMS,
        "telnet_platform_ids": TELNET_PLATFORM_IDS,
        "telnet_platform_base_ids": [normalize_platform_id(pid) for pid in TELNET_PLATFORM_IDS],
        "current_user": user,
        "is_admin": role == "admin",
        "is_operator": role in ("admin", "operator"),
    }
