from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Form, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi_csrf_protect import CsrfProtect

from app import crud
from app.models import User
from app.core.settings import settings
from app.db import session_scope
from app.routers.common import _current_user, _layout_context, _log_action, _require_admin, templates
from app.services.auth import create_session_token


router = APIRouter()


@router.get("/login")
def login_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    next_raw = request.query_params.get("next") or "/dashboard"
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        "login.html",
        {
            **_layout_context(request=request, active=""),
            "next": next_raw,
            "err": request.query_params.get("err") or "",
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/login")
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

        # 提前获取需要的字段，因为 session 提交后 user 对象会过期/分离
        user_id = user.id
        password_expired = user.password_expired

        crud.create_login_log(
            session,
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            status="success",
        )
    
    token = create_session_token(user_id=int(user_id), ttl_seconds=settings.session_ttl_seconds)
    
    # 如果密码已过期，强制跳转到修改密码页面
    if password_expired:
        resp = RedirectResponse(url="/change-password", status_code=303)
    else:
        resp = RedirectResponse(url=unquote(next or "/dashboard"), status_code=303)
        
    resp.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return resp


@router.get("/change-password")
def change_password_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        "change_password.html",
        {
            **_layout_context(request=request, active=""),
            "err": request.query_params.get("err") or "",
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/change-password")
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


@router.get("/profile")
def profile_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    user = _current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        "profile.html",
        {
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


@router.post("/profile/change-password")
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


@router.get("/logout")
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


@router.get("/users")
def users_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_admin(request)
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        total = crud.count_users(session)
        items = crud.list_users(session, limit=limit, offset=offset)
        groups = crud.list_groups(session)
        edit_id = request.query_params.get("edit")
        current = None
        current_allowed_ids = set()
        if edit_id and edit_id.isdigit():
            current = crud.get_user(session, int(edit_id))
            if current and current.allowed_group_ids:
                # Store as strings for easy comparison in template
                current_allowed_ids = {x.strip() for x in current.allowed_group_ids.split(",") if x.strip()}
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/users?limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/users?limit={limit}&page="
         else:
             pagination_base = "/users?page="

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        "users.html",
        {
            **_layout_context(request=request, active="users"), 
            "csrf_token": csrf_token,
            "items": items, 
            "current": current,
            "groups": groups,
            "current_allowed_ids": current_allowed_ids,
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


@router.post("/users")
def upsert_user(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    user_id: int = Form(0),
    username: str = Form(...),
    role: str = Form("readonly"),
    password: str = Form(""),
    group_access_type: str = Form("all"),
    allowed_group_ids: list[int] = Form([]),
):
    csrf_protect.validate_csrf(request)
    _require_admin(request)
    
    # Process allowed_group_ids
    allowed_ids_str = None
    if group_access_type == "specific":
        unique_ids = sorted(list(set(allowed_group_ids)))
        if unique_ids:
            allowed_ids_str = ",".join(str(x) for x in unique_ids)
        else:
            allowed_ids_str = ""

    with session_scope() as session:
        if user_id and int(user_id) > 0:
            try:
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
                )
                _log_action(request, session, "CREATE_USER", "user", user.id, f"Username: {username}, Role: {role}, Access: {group_access_type}")
            except RuntimeError as exc:
                return RedirectResponse(url=f"/users?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/users?msg=已保存", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int, csrf_protect: CsrfProtect = Depends()):
    csrf_protect.validate_csrf(request)
    admin = _require_admin(request)
    if int(admin.id) == int(user_id):
        return RedirectResponse(url="/users?err=不能删除当前登录用户", status_code=303)
    with session_scope() as session:
        user = crud.get_user(session, user_id)
        username = user.username if user else f"ID: {user_id}"
        crud.delete_user(session, user_id)
        _log_action(request, session, "DELETE_USER", "user", user_id, f"Username: {username}")
    return RedirectResponse(url="/users?msg=已删除", status_code=303)
