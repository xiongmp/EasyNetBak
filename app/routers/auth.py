from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
from urllib.parse import quote, unquote

from fastapi import APIRouter, Form, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi_csrf_protect import CsrfProtect
import qrcode

from app import crud
from app.models import User
from app.core.settings import settings
from app.db import session_scope
from app.routers.common import _current_user, _layout_context, _log_action, _require_permission, templates
from app.services.auth import (
    build_mfa_uri,
    create_session_token,
    generate_recovery_codes,
    generate_mfa_secret,
    hash_recovery_code,
    normalize_recovery_code,
    verify_mfa,
)


router = APIRouter(tags=["认证授权 (Auth)"])
_PENDING_2FA_COOKIE = "pending_2fa"


def _sign_mfa_secret(secret: str) -> str:
    key = (settings.secret_key or "").encode("utf-8")
    return hmac.new(key, secret.encode("utf-8"), hashlib.sha256).hexdigest()


def _mfa_qr_data_uri(uri: str) -> str:
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{payload}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "==")


def _create_pending_token(*, user_id: int, ttl_seconds: int = 300) -> str:
    if not settings.secret_key:
        raise RuntimeError("settings.secret_key is required for sessions")
    now = int(time.time())
    payload = {"v": 1, "uid": int(user_id), "exp": now + int(ttl_seconds), "t": "2fa"}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def _decode_pending_token(token: str) -> dict[str, int] | None:
    if not token or "." not in token or not settings.secret_key:
        return None
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64d(raw_b64)
        sig = _b64d(sig_b64)
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("v", 0)) != 1:
            return None
        if payload.get("t") != "2fa":
            return None
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            return None
        uid = int(payload.get("uid", 0))
        if uid <= 0:
            return None
        return payload
    except Exception:
        return None


def _users_page_response(
    request: Request,
    csrf_protect: CsrfProtect,
    *,
    recovery_codes: list[str] | None = None,
    edit_id: int | None = None,
):
    _require_permission(request, "users.view")
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        crud.ensure_default_roles(session)
        total = crud.count_users(session)
        items = crud.list_users(session, limit=limit, offset=offset)
        groups = crud.list_groups(session)
        roles = crud.list_roles(session)
        role_map = {r.code: r for r in roles}
        edit_id_raw = str(edit_id) if edit_id is not None else request.query_params.get("edit")
        current = None
        current_allowed_ids = set()
        current_recovery_count = 0
        if edit_id_raw and edit_id_raw.isdigit():
            current = crud.get_user(session, int(edit_id_raw))
            if current and current.allowed_group_ids:
                current_allowed_ids = {x.strip() for x in current.allowed_group_ids.split(",") if x.strip()}
            if current and crud.is_admin_role_code(getattr(current, "role", "")):
                current_recovery_count = len(getattr(current, "recovery_codes", []) or [])
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/users?limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/users?limit={limit}&page="
         else:
             pagination_base = "/users?page="

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    response = templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            **_layout_context(request=request, active="users"), 
            "csrf_token": csrf_token,
            "items": items, 
            "current": current,
            "groups": groups,
            "roles": roles,
            "role_map": role_map,
            "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
            "current_allowed_ids": current_allowed_ids,
            "recovery_codes": recovery_codes or [],
            "recovery_codes_count": current_recovery_count,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.get("/login", summary="登录页面", description="用户登录界面")
def login_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    next_raw = request.query_params.get("next") or "/dashboard"
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            **_layout_context(request=request, active=""),
            "next": next_raw,
            "err": request.query_params.get("err") or "",
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/login", summary="用户登录", description="处理用户登录请求，包含密码验证和 MFA 校验逻辑")
def login_submit(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
):
    csrf_protect.validate_csrf(request)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    require_mfa_setup = False
    require_mfa_verify = False

    with session_scope() as session:
        user = crud.authenticate_user(session, username=username, password=password)
        if user is None:
            crud.create_login_log(
                session,
                username=username,
                ip_address=ip_address,
                user_agent=user_agent,
                status="fail",
                fail_reason="Invalid credentials",
            )
            return RedirectResponse(url="/login?err=账号或密码错误", status_code=303)

        if user.mfa_enabled:
            if not user.mfa_secret:
                require_mfa_setup = True
            else:
                require_mfa_verify = True

        user_id = user.id
        password_expired = user.password_expired

        if password_expired:
            require_mfa_verify = False

        if not require_mfa_verify:
            crud.create_login_log(
                session,
                username=username,
                ip_address=ip_address,
                user_agent=user_agent,
                status="success",
            )
    
    # 如果密码已过期，强制跳转到修改密码页面
    if password_expired:
        resp = RedirectResponse(url="/change-password", status_code=303)
    elif require_mfa_setup:
        resp = RedirectResponse(url="/mfa-setup", status_code=303)
    elif require_mfa_verify:
        nxt = quote(next or "/dashboard")
        resp = RedirectResponse(url=f"/mfa-verify?next={nxt}", status_code=303)
    else:
        resp = RedirectResponse(url=unquote(next or "/dashboard"), status_code=303)

    if require_mfa_verify:
        pending_token = _create_pending_token(user_id=int(user_id), ttl_seconds=300)
        resp.set_cookie(
            _PENDING_2FA_COOKIE,
            pending_token,
            httponly=True,
            samesite=settings.auth_cookie_samesite,
            secure=settings.auth_cookie_secure,
            max_age=300,
            path="/",
        )
        resp.delete_cookie(settings.auth_cookie_name, path="/")
    else:
        token = create_session_token(user_id=int(user_id), ttl_seconds=settings.session_ttl_seconds)
        max_age = settings.session_ttl_seconds if settings.auth_cookie_persistent else None
        resp.set_cookie(
            settings.auth_cookie_name,
            token,
            httponly=True,
            samesite=settings.auth_cookie_samesite,
            secure=settings.auth_cookie_secure,
            max_age=max_age,
            path="/",
        )
        resp.delete_cookie(_PENDING_2FA_COOKIE, path="/")
    return resp


@router.get("/mfa-setup", summary="MFA 设置页面", description="多因素认证(MFA)配置页面，生成并展示绑定二维码")
def mfa_setup_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not user.mfa_enabled:
        return RedirectResponse(url="/dashboard", status_code=303)
    if user.mfa_secret:
        return RedirectResponse(url="/dashboard", status_code=303)

    secret = generate_mfa_secret()
    issuer = settings.app_name or "Network Backup"
    uri = build_mfa_uri(secret=secret, username=user.username, issuer=issuer)
    qr_data = _mfa_qr_data_uri(uri)
    sig = _sign_mfa_secret(secret)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="mfa_setup.html",
        context={
            **_layout_context(request=request, active=""),
            "csrf_token": csrf_token,
            "mfa_secret": secret,
            "mfa_uri": uri,
            "mfa_qr": qr_data,
            "mfa_sig": sig,
            "err": request.query_params.get("err") or "",
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/mfa-setup", summary="提交 MFA 设置", description="校验 MFA 验证码并启用多因素认证")
def mfa_setup_submit(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    mfa_secret: str = Form(""),
    mfa_sig: str = Form(""),
    mfa_code: str = Form(""),
):
    csrf_protect.validate_csrf(request)
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not user.mfa_enabled:
        return RedirectResponse(url="/dashboard", status_code=303)
    if user.mfa_secret:
        return RedirectResponse(url="/dashboard", status_code=303)

    secret = (mfa_secret or "").strip()
    sig = (mfa_sig or "").strip()
    code = (mfa_code or "").strip()
    if not secret or not sig or not hmac.compare_digest(_sign_mfa_secret(secret), sig):
        return RedirectResponse(url="/mfa-setup?err=MFA配置已过期，请重新加载", status_code=303)
    if not verify_mfa(secret, code):
        return RedirectResponse(url="/mfa-setup?err=验证码错误，请重试", status_code=303)

    with session_scope() as session:
        crud.update_user(session, int(user.id), mfa_enabled=True, mfa_secret=secret)
        _log_action(request, session, "ENABLE_MFA", "user", user.id, f"User {user.username} configured MFA")
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/mfa-verify", summary="MFA 验证页面", description="用户登录时进行多因素认证或恢复码验证的页面")
def mfa_verify_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    token = request.cookies.get(_PENDING_2FA_COOKIE, "")
    payload = _decode_pending_token(token)
    if not payload:
        return RedirectResponse(url="/login?err=请先登录", status_code=303)
    allow_recovery = False
    with session_scope() as session:
        user = crud.get_user(session, int(payload.get("uid", 0)))
        if (
            user
            and crud.is_admin_role_code(getattr(user, "role", ""))
            and user.recovery_codes_enabled
            and user.recovery_codes
        ):
            allow_recovery = True
    next_raw = request.query_params.get("next") or "/dashboard"
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="mfa_verify.html",
        context={
            **_layout_context(request=request, active=""),
            "csrf_token": csrf_token,
            "next": next_raw,
            "err": request.query_params.get("err") or "",
            "allow_recovery": allow_recovery,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/mfa-verify", summary="提交 MFA 验证", description="处理 MFA 验证码或恢复码的校验逻辑")
def mfa_verify_submit(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    mfa_code: str = Form(""),
    recovery_code: str = Form(""),
    next: str = Form("/dashboard"),
):
    csrf_protect.validate_csrf(request)
    token = request.cookies.get(_PENDING_2FA_COOKIE, "")
    payload = _decode_pending_token(token)
    if not payload:
        return RedirectResponse(url="/login?err=登录已过期", status_code=303)
    user_id = int(payload.get("uid", 0))
    with session_scope() as session:
        user = crud.get_user(session, user_id)
        if not user or not user.mfa_enabled:
            return RedirectResponse(url="/login?err=账号状态异常", status_code=303)
        if (recovery_code or "").strip():
            if not crud.is_admin_role_code(getattr(user, "role", "")):
                return RedirectResponse(url="/mfa-verify?err=恢复码不可用", status_code=303)
            if not user.recovery_codes_enabled:
                return RedirectResponse(url="/mfa-verify?err=恢复码不可用", status_code=303)
            codes = user.recovery_codes or []
            if not codes:
                return RedirectResponse(url="/mfa-verify?err=恢复码不可用", status_code=303)
            normalized = normalize_recovery_code(recovery_code)
            if not normalized:
                return RedirectResponse(url="/mfa-verify?err=恢复码不可用", status_code=303)
            hashed = hash_recovery_code(normalized)
            if not any(hmac.compare_digest(hashed, item) for item in codes):
                crud.create_login_log(
                    session,
                    username=user.username,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    status="fail",
                    fail_reason="Invalid recovery code",
                )
                return RedirectResponse(url="/mfa-verify?err=恢复码错误或已失效", status_code=303)
            remaining = [item for item in codes if not hmac.compare_digest(hashed, item)]
            crud.update_user(session, int(user_id), recovery_codes=remaining)
            _log_action(request, session, "USE_RECOVERY_CODE", "user", user_id, f"Username: {user.username}")
        else:
            code = (mfa_code or "").strip()
            if not user.mfa_secret or not verify_mfa(user.mfa_secret, code):
                crud.create_login_log(
                    session,
                    username=user.username,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    status="fail",
                    fail_reason="Invalid MFA",
                )
                return RedirectResponse(url="/mfa-verify?err=MFA验证码错误", status_code=303)
        crud.create_login_log(
            session,
            username=user.username,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            status="success",
        )

    auth_token = create_session_token(user_id=int(user_id), ttl_seconds=settings.session_ttl_seconds)
    resp = RedirectResponse(url=unquote(next or "/dashboard"), status_code=303)
    max_age = settings.session_ttl_seconds if settings.auth_cookie_persistent else None
    resp.set_cookie(
        settings.auth_cookie_name,
        auth_token,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
        secure=settings.auth_cookie_secure,
        max_age=max_age,
        path="/",
    )
    resp.delete_cookie(_PENDING_2FA_COOKIE, path="/")
    return resp


@router.get("/change-password", summary="修改密码页面", description="用户强制修改密码的页面")
def change_password_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="change_password.html",
        context={
            **_layout_context(request=request, active=""),
            "err": request.query_params.get("err") or "",
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/change-password", summary="提交密码修改", description="验证并更新用户密码")
def change_password_submit(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    csrf_protect.validate_csrf(request)
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    if new_password != confirm_password:
        return RedirectResponse(url="/change-password?err=两次输入的密码不一致", status_code=303)
    
    if len(new_password) < 5:
        return RedirectResponse(url="/change-password?err=密码长度至少为5位", status_code=303)

    with session_scope() as session:
        # 更新用户密码并重置过期标志
        crud.update_user(session, user.id, password=new_password)
        
        db_user = session.get(User, user.id)
        if db_user:
            db_user.password_expired = False
            session.add(db_user)
            session.commit()
            _log_action(request, session, "CHANGE_PASSWORD", "user", user.id, f"User {user.username} changed expired password")

    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/profile", summary="个人设置页面", description="管理个人信息和安全选项")
def profile_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            **_layout_context(request=request, active="profile"),
            "page_title": "个人设置",
            "page_subtitle": "管理您的个人信息和安全选项",
            "user": user,
            "msg": request.query_params.get("msg") or "",
            "err": request.query_params.get("err") or "",
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/profile/change-password", summary="修改个人密码", description="验证旧密码并更新当前用户的密码")
def profile_change_password(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    csrf_protect.validate_csrf(request)
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    if new_password != confirm_password:
        return RedirectResponse(url="/profile?err=两次输入的密码不一致", status_code=303)
    
    if len(new_password) < 5:
        return RedirectResponse(url="/profile?err=密码长度至少为5位", status_code=303)

    with session_scope() as session:
        # 验证旧密码
        db_user = crud.authenticate_user(session, username=user.username, password=old_password)
        if not db_user:
            return RedirectResponse(url="/profile?err=当前密码错误", status_code=303)
            
        # 更新密码
        crud.update_user(session, user.id, password=new_password)
        _log_action(request, session, "CHANGE_PASSWORD", "user", user.id, f"User {user.username} changed password via profile")

    return RedirectResponse(url="/profile?msg=密码已修改", status_code=303)


@router.get("/logout", summary="退出登录", description="注销当前用户并清除 Session")
def logout(request: Request):
    user = _current_user(request)
    if user:
        with session_scope() as session:
             crud.create_login_log(
                session,
                username=user.username,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                status="logout",
            )
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(settings.auth_cookie_name, path="/")
    return resp


@router.get("/users", summary="用户管理页面", description="查看系统中所有用户的列表及分页信息", tags=["系统设置 (System)"])
def users_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    return _users_page_response(request, csrf_protect)


@router.post("/users", summary="创建或更新用户", description="新增或修改用户信息、角色和权限", tags=["系统设置 (System)"])
def upsert_user(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    user_id: int = Form(0),
    username: str = Form(...),
    role: str = Form("readonly"),
    password: str = Form(""),
    group_access_type: str = Form("all"),
    allowed_group_ids: list[int] = Form([]),
    mfa_enabled: str | None = Form(None),
    mfa_reset: str | None = Form(None),
    recovery_codes_generate: str | None = Form(None),
    recovery_codes_enabled: str | None = Form(None),
):
    csrf_protect.validate_csrf(request)
    if user_id and int(user_id) > 0:
        _require_permission(request, "users.update")
    else:
        _require_permission(request, "users.create")
    role = (role or "").strip().lower()
    
    # Process allowed_group_ids
    allowed_ids_str = None
    if group_access_type == "specific":
        unique_ids = sorted(list(set(allowed_group_ids)))
        if unique_ids:
            allowed_ids_str = ",".join(str(x) for x in unique_ids)
        else:
            allowed_ids_str = ""

    enable_mfa = (mfa_enabled or "").lower() in {"1", "true", "on", "yes"}
    reset_mfa = (mfa_reset or "").lower() in {"1", "true", "on", "yes"}
    generate_recovery = (recovery_codes_generate or "").lower() in {"1", "true", "on", "yes"}
    enable_recovery = (recovery_codes_enabled or "").lower() in {"1", "true", "on", "yes"}

    with session_scope() as session:
        crud.ensure_default_roles(session)
        if user_id and int(user_id) > 0:
            try:
                target = crud.get_user(session, int(user_id))
                if target and target.username == "admin":
                    update_payload = {"mfa_enabled": enable_mfa}
                    if enable_mfa:
                        if reset_mfa:
                            update_payload["mfa_secret"] = None
                    else:
                        update_payload["mfa_secret"] = None
                    if password:
                        update_payload["password"] = password
                    update_payload["recovery_codes_enabled"] = enable_recovery
                    if not enable_recovery:
                        update_payload["recovery_codes"] = []
                    if generate_recovery:
                        codes = generate_recovery_codes()
                        update_payload["recovery_codes"] = [hash_recovery_code(c) for c in codes]
                        update_payload["recovery_codes_enabled"] = True
                    crud.update_user(session, int(user_id), **update_payload)
                    _log_action(request, session, "UPDATE_USER", "user", user_id, "Username: admin, Password/MFA")
                    if generate_recovery:
                        _log_action(request, session, "RESET_RECOVERY_CODES", "user", user_id, "Username: admin")
                        return _users_page_response(request, csrf_protect, recovery_codes=codes, edit_id=int(user_id))
                    return RedirectResponse(url="/users?msg=已保存", status_code=303)
                if target:
                    if not enable_mfa:
                        crud.update_user(
                            session,
                            int(user_id),
                            username=username,
                            role=role,
                            password=password or None,
                            group_access_type=group_access_type,
                            allowed_group_ids=allowed_ids_str,
                            mfa_enabled=False,
                            mfa_secret=None,
                        )
                        _log_action(request, session, "UPDATE_USER", "user", user_id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
                        return RedirectResponse(url="/users?msg=已保存", status_code=303)

                    if target.mfa_enabled and not reset_mfa:
                        update_payload = {
                            "username": username,
                            "role": role,
                            "password": password or None,
                            "group_access_type": group_access_type,
                            "allowed_group_ids": allowed_ids_str,
                            "mfa_enabled": True,
                        }
                        if generate_recovery and crud.is_admin_role_code(target.role):
                            codes = generate_recovery_codes()
                            update_payload["recovery_codes"] = [hash_recovery_code(c) for c in codes]
                            update_payload["recovery_codes_enabled"] = True
                        elif crud.is_admin_role_code(target.role):
                            update_payload["recovery_codes_enabled"] = enable_recovery
                            if not enable_recovery:
                                update_payload["recovery_codes"] = []
                        crud.update_user(session, int(user_id), **update_payload)
                        _log_action(request, session, "UPDATE_USER", "user", user_id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
                        if generate_recovery and crud.is_admin_role_code(target.role):
                            _log_action(request, session, "RESET_RECOVERY_CODES", "user", user_id, f"Username: {username}")
                            return _users_page_response(request, csrf_protect, recovery_codes=codes, edit_id=int(user_id))
                        return RedirectResponse(url="/users?msg=已保存", status_code=303)
                    update_payload = {
                        "username": username,
                        "role": role,
                        "password": password or None,
                        "group_access_type": group_access_type,
                        "allowed_group_ids": allowed_ids_str,
                        "mfa_enabled": True,
                        "mfa_secret": None,
                    }
                    if generate_recovery and crud.is_admin_role_code(target.role):
                        codes = generate_recovery_codes()
                        update_payload["recovery_codes"] = [hash_recovery_code(c) for c in codes]
                        update_payload["recovery_codes_enabled"] = True
                    elif crud.is_admin_role_code(target.role):
                        update_payload["recovery_codes_enabled"] = enable_recovery
                        if not enable_recovery:
                            update_payload["recovery_codes"] = []
                    crud.update_user(session, int(user_id), **update_payload)
                    _log_action(request, session, "UPDATE_USER", "user", user_id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
                    if generate_recovery and crud.is_admin_role_code(target.role):
                        _log_action(request, session, "RESET_RECOVERY_CODES", "user", user_id, f"Username: {username}")
                        return _users_page_response(request, csrf_protect, recovery_codes=codes, edit_id=int(user_id))
                    return RedirectResponse(url="/users?msg=已保存", status_code=303)
                crud.update_user(
                    session,
                    int(user_id),
                    username=username,
                    role=role,
                    password=password or None,
                    group_access_type=group_access_type,
                    allowed_group_ids=allowed_ids_str,
                )
                _log_action(request, session, "UPDATE_USER", "user", user_id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
            except RuntimeError as exc:
                return RedirectResponse(url=f"/users?err={str(exc)}", status_code=303)
        else:
            if not password:
                return RedirectResponse(url="/users?err=新建用户必须设置密码", status_code=303)
            try:
                user = crud.create_user(
                    session, 
                    username=username.strip(), 
                    password=password, 
                    role=role,
                    group_access_type=group_access_type,
                    allowed_group_ids=allowed_ids_str,
                    mfa_enabled=enable_mfa,
                    mfa_secret=None,
                )
                _log_action(request, session, "CREATE_USER", "user", user.id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
            except RuntimeError as exc:
                return RedirectResponse(url=f"/users?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/users?msg=已保存", status_code=303)


@router.post("/users/{user_id}/delete", summary="删除用户", description="删除指定用户（admin不可删除）", tags=["系统设置 (System)"])
def delete_user(request: Request, user_id: int, csrf_protect: CsrfProtect = Depends()):
    csrf_protect.validate_csrf(request)
    current = _require_permission(request, "users.delete")
    if int(current.id) == int(user_id):
        return RedirectResponse(url="/users?err=不能删除当前登录用户", status_code=303)
    with session_scope() as session:
        user = crud.get_user(session, user_id)
        username = user.username if user else f"ID: {user_id}"
        if user and user.username == "admin":
            return RedirectResponse(url="/users?err=admin 用户不可删除", status_code=303)
        crud.delete_user(session, user_id)
        _log_action(request, session, "DELETE_USER", "user", user_id, f"Username: {username}")
    return RedirectResponse(url="/users?msg=已删除", status_code=303)


@router.get("/roles", summary="角色管理页面", description="查看系统中所有角色的列表", tags=["系统设置 (System)"])
def roles_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_permission(request, "roles.view")
    with session_scope() as session:
        crud.ensure_default_roles(session)
        items = crud.list_roles(session)
        usage = {r.code: crud.role_usage_count(session, r.code) for r in items}
        admin_codes = set(getattr(crud, "ROLE_ADMIN_CODES", set()))
        for role in items:
            if role.code in admin_codes:
                continue
            normalized = crud.normalize_permission_codes((role.permissions or "").split(","))
            role.permissions = ",".join(normalized) if normalized else None

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    permission_catalog = crud.list_permission_catalog()
    permission_group_labels = {
        "dashboard": "仪表盘",
        "devices": "设备管理",
        "groups": "分组管理",
        "credentials": "凭据管理",
        "templates": "模板管理",
        "backups": "备份历史",
        "config_search": "配置搜索",
        "schedules": "定时任务",
        "audit_logs": "操作日志",
        "webshell_records": "Webshell回放",
        "login_logs": "登录日志",
        "diff_rules": "Diff 规则",
        "notifications": "系统管理",
        "settings": "系统管理",
        "storage_settings": "系统管理",
        "api_keys": "系统管理",
        "users": "用户管理",
        "roles": "角色管理",
    }

    response = templates.TemplateResponse(
        request=request,
        name="roles.html",
        context={
            **_layout_context(request=request, active="roles"),
            "items": items,
            "usage": usage,
            "csrf_token": csrf_token,
            "permission_catalog": permission_catalog,
            "permission_group_labels": permission_group_labels,
            "admin_role_codes": list(getattr(crud, "ROLE_ADMIN_CODES", set())),
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/roles", summary="创建或更新角色", description="新增或修改角色及其权限配置", tags=["系统设置 (System)"])
def upsert_role(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    role_id: int = Form(0),
    code: str = Form(""),
    name: str = Form(...),
    permission_codes: list[str] = Form([]),
):
    csrf_protect.validate_csrf(request)
    if role_id and int(role_id) > 0:
        _require_permission(request, "roles.update")
    else:
        _require_permission(request, "roles.create")

    role_code = (code or "").strip().lower()
    role_name = (name or "").strip()
    if not role_name:
        return RedirectResponse(url="/roles?err=角色名称不能为空", status_code=303)

    normalized_permissions = crud.normalize_permission_codes(permission_codes)
    permissions_str = ",".join(normalized_permissions) if normalized_permissions else None

    with session_scope() as session:
        crud.ensure_default_roles(session)
        if role_id and int(role_id) > 0:
            role = crud.get_role(session, int(role_id))
            if role is None:
                return RedirectResponse(url="/roles?err=角色不存在", status_code=303)
            if crud.is_admin_role_code(role.code):
                if role_code and role_code != role.code:
                    return RedirectResponse(url="/roles?err=系统管理员角色标识不可修改", status_code=303)
                crud.update_role(session, int(role_id), name=role_name, permissions=None)
                _log_action(request, session, "UPDATE_ROLE", "role", role_id, f"Name: {role_name}, Code: {role.code}")
            else:
                if not role_code:
                    return RedirectResponse(url="/roles?err=角色标识不能为空", status_code=303)
                try:
                    updated = crud.update_role(session, int(role_id), code=role_code, name=role_name, permissions=permissions_str)
                except RuntimeError as exc:
                    return RedirectResponse(url=f"/roles?err={str(exc)}", status_code=303)
                updated_code = updated.code if updated else role_code
                _log_action(request, session, "UPDATE_ROLE", "role", role_id, f"Name: {role_name}, Code: {updated_code}")
        else:
            if not role_code:
                return RedirectResponse(url="/roles?err=角色标识不能为空", status_code=303)
            if crud.is_admin_role_code(role_code):
                return RedirectResponse(url="/roles?err=系统管理员角色不可新建", status_code=303)
            try:
                role = crud.create_role(
                    session,
                    code=role_code,
                    name=role_name,
                    permissions=permissions_str,
                    is_system=False,
                    is_admin=False,
                )
                _log_action(request, session, "CREATE_ROLE", "role", role.id, f"Name: {role.name}, Code: {role.code}")
            except RuntimeError as exc:
                return RedirectResponse(url=f"/roles?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/roles?msg=已保存", status_code=303)


@router.post("/roles/{role_id}/delete", summary="删除角色", description="删除指定角色（系统内置角色不可删除）", tags=["系统设置 (System)"])
def delete_role(request: Request, role_id: int, csrf_protect: CsrfProtect = Depends()):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "roles.delete")
    with session_scope() as session:
        crud.ensure_default_roles(session)
        role = crud.get_role(session, int(role_id))
        if role is None:
            return RedirectResponse(url="/roles?err=角色不存在", status_code=303)
        if crud.is_admin_role_code(role.code):
            return RedirectResponse(url="/roles?err=系统管理员角色不可删除", status_code=303)
        usage = crud.role_usage_count(session, role.code)
        if usage > 0:
            return RedirectResponse(url=f"/roles?err=该角色已分配给 {usage} 个用户，无法删除", status_code=303)
        crud.delete_role(session, int(role_id))
        _log_action(request, session, "DELETE_ROLE", "role", role_id, f"Name: {role.name}, Code: {role.code}")
    return RedirectResponse(url="/roles?msg=已删除", status_code=303)
