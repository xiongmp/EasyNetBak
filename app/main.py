from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from starlette.concurrency import run_in_threadpool

from app import crud
from app.core.settings import settings
from app.db import init_db, session_scope
from app.scheduler import ensure_default_schedule_from_legacy_settings, stop_scheduler, sync_scheduler_from_db
from app.router_registry import router as web_router
from app.routers.support import ApiError
from app.services.auth import decode_session_token
from app.core.logger import setup_logging, set_request_id
from app.services import identity_service, request_context_service


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    with session_scope() as session:
        identity_service.ensure_default_roles(session)
        if identity_service.count_users(session) == 0:
            identity_service.create_user(
                session,
                username=settings.bootstrap_admin_username,
                password=settings.bootstrap_admin_password,
                role="admin",
                password_expired=True,
            )
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

    try:
        yield
    except asyncio.CancelledError:
        # On Windows, stopping uvicorn during reload can cancel lifespan shutdown.
        # Treat it as a normal shutdown path to avoid noisy terminal tracebacks.
        pass
    finally:
        stop_scheduler()

app = FastAPI(
    title=settings.app_name,
    description="Network Backup 系统 API 接口文档，包含设备管理、分组管理、凭据管理等功能。",
    version=settings.app_version.lstrip("vV"),
    openapi_tags=[
        {"name": "设备管理"},
        {"name": "分组管理"},
        {"name": "凭据管理"},
        {"name": "备份管理"},
        {"name": "其它"},
    ],
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@CsrfProtect.load_config
def get_csrf_config():
    return settings.csrf


@app.exception_handler(CsrfProtectError)
def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(ApiError)
def api_error_exception_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/vendor/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/vendor/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/static/img/favicon.svg",
    )


@app.middleware("http")
async def _request_id_middleware(request, call_next):
    set_request_id()
    response = await call_next(request)
    return response


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

    request_context_service.apply_request_context(
        request,
        request_context_service.build_default_request_context(),
    )

    if not skip_auth:
        api_key_str = request.headers.get("X-API-Key")
        if path.startswith("/api/v1/"):
            # External APIs: Strictly require API Key, do not fall back to Session Cookie
            # This prevents CSRF vulnerabilities on API endpoints that don't check CSRF tokens.
            request_context = await run_in_threadpool(
                request_context_service.load_auth_context,
                user_id=0,
                api_key_str=api_key_str or None,
            )
        else:
            # Internal UI/Web routes: Accept API Key or Session Cookie
            if api_key_str:
                request_context = await run_in_threadpool(
                    request_context_service.load_auth_context,
                    user_id=0,
                    api_key_str=api_key_str,
                )
            else:
                token = request.cookies.get(settings.auth_cookie_name, "")
                payload = decode_session_token(token)
                uid = int(payload.get("uid", 0)) if payload else 0
                request_context = await run_in_threadpool(
                    request_context_service.load_auth_context,
                    user_id=uid,
                    api_key_str=None,
                )

        request_context_service.apply_request_context(request, request_context)

    user = request.state.user

    if skip_auth or allow_anonymous:
        return await call_next(request)

    if user is None:
        if path.startswith("/api/v1/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized", "code": "UNAUTHORIZED"},
            )
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
