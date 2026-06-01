from __future__ import annotations

from ipaddress import ip_address
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
    def _normalize_ip_candidate(value: str | None) -> str | None:
        if not value:
            return None
        candidate = value.strip().strip('"').strip("'")
        if not candidate or candidate.lower() == "unknown":
            return None
        if candidate.startswith("[") and "]" in candidate:
            candidate = candidate[1:candidate.index("]")]
        try:
            ip_address(candidate)
            return candidate
        except ValueError:
            return None

    def _extract_from_x_forwarded_for(value: str | None) -> str | None:
        if not value:
            return None
        for part in value.split(","):
            candidate = _normalize_ip_candidate(part)
            if candidate:
                return candidate
        return None

    def _extract_from_forwarded(value: str | None) -> str | None:
        if not value:
            return None
        for item in value.split(","):
            for segment in item.split(";"):
                key, sep, raw = segment.partition("=")
                if sep != "=" or key.strip().lower() != "for":
                    continue
                candidate = raw.strip()
                if candidate.startswith('"') and candidate.endswith('"'):
                    candidate = candidate[1:-1]
                candidate = candidate.strip()
                if candidate.lower() == "unknown":
                    continue
                if candidate.startswith("[") and "]" in candidate:
                    candidate = candidate[1:candidate.index("]")]
                elif candidate.count(":") == 1 and "." in candidate:
                    host, _, port = candidate.partition(":")
                    if port.isdigit():
                        candidate = host
                normalized = _normalize_ip_candidate(candidate)
                if normalized:
                    return normalized
        return None

    header_extractors = (
        ("CF-Connecting-IP", _normalize_ip_candidate),
        ("True-Client-IP", _normalize_ip_candidate),
        ("X-Original-Forwarded-For", _extract_from_x_forwarded_for),
        ("X-Forwarded-For", _extract_from_x_forwarded_for),
        ("X-Real-IP", _normalize_ip_candidate),
        ("Forwarded", _extract_from_forwarded),
    )

    for header_name, extractor in header_extractors:
        ip = extractor(request.headers.get(header_name))
        if ip:
            return ip

    # 最后回退到直接连接的 client host
    return _normalize_ip_candidate(request.client.host) if request.client else None


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
