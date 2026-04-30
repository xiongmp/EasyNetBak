from __future__ import annotations

from typing import Any, Iterable, NoReturn, Union

from fastapi import HTTPException, Request, WebSocket
from sqlmodel import Session

from app import crud
from app.services import identity_service


class ApiError(Exception):
    def __init__(self, *, status_code: int, detail: str, code: str = "API_ERROR"):
        self.status_code = int(status_code)
        self.detail = detail
        self.code = code
        super().__init__(detail)


def _permission_codes() -> set[str]:
    try:
        return {x["code"] for x in getattr(crud, "PERMISSION_CATALOG", [])}
    except Exception:
        return set()


def _user_effective_perms(user) -> set[str] | None:
    if not user:
        return set()
    if identity_service.is_admin_role_code(getattr(user, "role", "")):
        return None
    valid = _permission_codes()
    eff = identity_service.get_effective_permission_codes(user)
    return {c for c in eff if c in valid}


def has_permission(user, code: str) -> bool:
    if not user:
        return False
    if identity_service.is_admin_role_code(getattr(user, "role", "")):
        return True
    eff = _user_effective_perms(user)
    return bool(eff and (code in eff))


def _current_user(request: Request):
    return getattr(request.state, "user", None)


def _require_permission(request: Request, code: str):
    user = _current_user(request)
    if not has_permission(user, code):
        raise HTTPException(status_code=403, detail=f"Require permission: {code}")
    return user


def _require_any_permission(request: Request, codes: Iterable[str]):
    user = _current_user(request)
    for code in codes:
        if has_permission(user, code):
            return user
    raise HTTPException(status_code=403, detail="Require permission")


def raise_api_error(*, status_code: int, detail: str, code: str = "API_ERROR") -> NoReturn:
    raise ApiError(status_code=status_code, detail=detail, code=code)


def raise_service_api_error(exc) -> NoReturn:
    raise_api_error(
        status_code=int(getattr(exc, "status_code", 400)),
        detail=getattr(exc, "message", str(exc)),
        code=getattr(exc, "code", "SERVICE_ERROR"),
    )


def _require_api_permission(request: Request, code: str):
    user = _current_user(request)
    if not has_permission(user, code):
        raise_api_error(status_code=403, detail=f"Require permission: {code}", code="PERMISSION_DENIED")
    return user


def _require_admin(request: Request):
    user = _current_user(request)
    if not user or not identity_service.is_admin_role_code(getattr(user, "role", "")):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _require_operator(request: Request):
    user = _current_user(request)
    if not user or (not identity_service.is_admin_role_code(getattr(user, "role", "")) and getattr(user, "role", "") != "operator"):
        raise HTTPException(status_code=403, detail="Operator or Admin only")
    return user


def get_user_allowed_group_ids(user, session: Session | None = None) -> list[int] | None:
    if not user:
        return []
    if identity_service.is_admin_role_code(getattr(user, "role", "")):
        return None
    if getattr(user, "group_access_type", "all") == "all":
        return None

    ids = []
    for x in (getattr(user, "allowed_group_ids", "") or "").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    if session is None:
        return ids
    return crud.expand_group_ids(session, ids)


def get_remote_ip(request: Union[Request, WebSocket]) -> str | None:
    """
    从请求中获取真实 IP 地址，支持反向代理请求头。
    """
    # 尝试从 X-Forwarded-For 获取 (通常是第一个)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For 可能包含多个 IP，取第一个
        return forwarded_for.split(",")[0].strip()
    
    # 尝试从 X-Real-IP 获取
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # 最后回退到直接连接的 client host
    return request.client.host if request.client else None


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
        ip_address=get_remote_ip(request),
    )
