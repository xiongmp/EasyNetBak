from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from starlette.concurrency import run_in_threadpool
from sqlmodel import Session

from app import crud
from app.models import User
from app.core.settings import settings
from app.core.time import normalize_timezone_offset
from app.db import engine, init_db, session_scope
from app.scheduler import ensure_default_schedule_from_legacy_settings, stop_scheduler, sync_scheduler_from_db
from app.web import router as web_router
from app.services.auth import decode_session_token
from app.core.logger import setup_logging, set_request_id


app = FastAPI(
    title=settings.app_name,
    description="Network Backup 系统 API 接口文档，包含设备管理、备份任务、系统设置等功能。",
    version="1.0.0",
    openapi_tags=[
        {"name": "认证授权 (Auth)", "description": "用户登录、登出及多因素认证(MFA)相关接口"},
        {"name": "仪表盘 (Dashboard)", "description": "系统概览与仪表盘统计数据接口"},
        {"name": "设备管理 (Devices)", "description": "网络设备的增删改查、批量导入及连通性测试接口"},
        {"name": "资源管理 (Resources)", "description": "设备组、凭据及备份模板管理接口"},
        {"name": "备份管理 (Backups)", "description": "设备配置备份的执行、记录查看及对比分析接口"},
        {"name": "定时任务 (Schedules)", "description": "自动化备份计划任务管理接口"},
        {"name": "系统设置 (System)", "description": "用户管理、角色权限、存储配置、系统参数及审计日志接口"}
    ]
)


@CsrfProtect.load_config
def get_csrf_config():
    return settings.csrf


@app.exception_handler(CsrfProtectError)
def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)


@app.middleware("http")
async def _request_id_middleware(request, call_next):
    set_request_id()
    response = await call_next(request)
    return response


def _get_auth_context(uid: int) -> tuple[str | None, User | None]:
    """
    同步辅助函数：在线程池中运行，获取用户和时区设置。
    不使用 session_scope() 以避免 commit 导致的实例过期问题。
    """
    session = Session(engine)
    try:
        tz_str = crud.get_setting(session, key="timezone_offset")
        user = crud.get_user(session, uid) if uid > 0 else None
        if user:
            # 显式从 session 中移除，确保 session 关闭后对象仍然可用且数据保留
            session.refresh(user)
            session.expunge(user)
        return tz_str, user
    finally:
        session.close()


@app.middleware("http")
async def _auth_middleware(request, call_next):
    path = request.url.path
    skip_auth = (
        path.startswith("/static/")
        or path.startswith("/docs")
        or path in {"/openapi.json", "/redoc"}
        or path in {"/@vite/client"}
    )
    allow_anonymous = path in {"/login", "/logout", "/@vite/client", "/change-password", "/mfa-verify"}

    user = None
    request.state.user = None
    request.state.tz_offset = normalize_timezone_offset(settings.timezone_offset)
    off = request.state.tz_offset
    sign = -1 if off.startswith("-") else 1
    hh = int(off[1:3])
    mm = int(off[4:6])
    request.state.tz_offset_minutes = sign * (hh * 60 + mm)

    if not skip_auth:
        token = request.cookies.get(settings.auth_cookie_name, "")
        payload = decode_session_token(token)
        uid = int(payload.get("uid", 0)) if payload else 0
        
        # 使用 run_in_threadpool 在线程池中执行阻塞的数据库操作
        tz_str, user = await run_in_threadpool(_get_auth_context, uid)
        
        request.state.tz_offset = normalize_timezone_offset(tz_str, default=settings.timezone_offset)
        request.state.user = user
        off = request.state.tz_offset
        sign = -1 if off.startswith("-") else 1
        hh = int(off[1:3])
        mm = int(off[4:6])
        request.state.tz_offset_minutes = sign * (hh * 60 + mm)

    if skip_auth or allow_anonymous:
        return await call_next(request)

    if user is None:
        nxt = quote(str(request.url.path) + (("?" + request.url.query) if request.url.query else ""))
        response = RedirectResponse(url=f"/login?next={nxt}", status_code=303)
        if request.cookies.get(settings.auth_cookie_name):
            response.delete_cookie(settings.auth_cookie_name, path="/")
        return response

    # 强制修改密码逻辑：如果密码已过期且当前不在修改密码页面，则强制跳转
    if user.password_expired and path != "/change-password":
        return RedirectResponse(url="/change-password", status_code=303)

    if user.mfa_enabled and not user.mfa_secret and path not in {"/mfa-setup", "/logout", "/change-password"}:
        return RedirectResponse(url="/mfa-setup", status_code=303)

    return await call_next(request)


@app.on_event("startup")
def _on_startup() -> None:
    setup_logging()
    init_db()
    with session_scope() as session:
        crud.ensure_default_roles(session)
        if crud.count_users(session) == 0:
            crud.create_user(session, username=settings.bootstrap_admin_username, password=settings.bootstrap_admin_password, role="admin", password_expired=True)
    with session_scope() as session:
        enabled_str = crud.get_setting(session, key="schedule_enabled")
        crontab_str = crud.get_setting(session, key="backup_crontab")
    
    # 默认值：如果没有数据库设置，则默认禁用 schedule，默认 crontab 为 0 2 * * *
    # 如果数据库有值，优先使用数据库值
    # 注意：BackupSchedule 模型默认也是 enabled=False
    enabled = False if enabled_str is None else enabled_str in {"1", "true", "True", "yes", "YES"}
    crontab = crontab_str if crontab_str is not None else "0 2 * * *"
    
    ensure_default_schedule_from_legacy_settings(enabled=enabled, crontab=crontab)
    
    if settings.enable_scheduler:
        sync_scheduler_from_db()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    stop_scheduler()
