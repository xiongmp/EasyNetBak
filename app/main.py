from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from app import crud
from app.core.settings import settings
from app.db import init_db, session_scope
from app.scheduler import ensure_default_schedule_from_legacy_settings, stop_scheduler, sync_scheduler_from_db
from app.router_registry import router as web_router
from app.routers.support import ApiError
from app.schemas.api.common import public_api_error_response
from app.services.auth import decode_session_token
from app.core.logger import get_request_id, setup_logging, set_request_id
from app.services import identity_service, request_context_service, task_realtime_service
from app.i18n import get_current_locale, translate
from app.i18n.middleware import i18n_http_middleware, resolve_request_locale
from app.i18n.openapi import build_openapi_schema


from contextlib import asynccontextmanager


logger = logging.getLogger(__name__)


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
    await task_realtime_service.task_realtime_hub.start()

    try:
        yield
    except asyncio.CancelledError:
        # On Windows, stopping uvicorn during reload can cancel lifespan shutdown.
        # Treat it as a normal shutdown path to avoid noisy terminal tracebacks.
        pass
    finally:
        stop_scheduler()
        await task_realtime_service.task_realtime_hub.shutdown()

app = FastAPI(
    title="openapi.title",
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
    openapi_url=None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@CsrfProtect.load_config
def get_csrf_config():
    return settings.csrf


@app.exception_handler(CsrfProtectError)
def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    if _is_internal_json_api_request(request):
        return _api_error_json(
            status_code=exc.status_code,
            code="CSRF_VALIDATION_FAILED",
            message=exc.message,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def _is_public_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api/v1/")


def _is_internal_json_api_request(request: Request) -> bool:
    path = request.url.path
    return (
        (path.startswith("/api/") and not path.startswith("/api/v1/"))
        or path in {"/notifications/test", "/settings/test-s3", "/settings/test-ftp"}
    )


def _is_json_api_request(request: Request) -> bool:
    return _is_public_api_request(request) or _is_internal_json_api_request(request)


def _http_error_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "PERMISSION_DENIED",
        404: "RESOURCE_NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(int(status_code), "API_ERROR")


def _api_error_json(
    *,
    status_code: int,
    code: str,
    message: str = "",
    message_key: str | None = None,
    params: dict | None = None,
    details: dict | None = None,
    locale: str | None = None,
) -> JSONResponse:
    request_id = get_request_id() or set_request_id()
    response_locale = locale or get_current_locale()
    localized_message = translate(
        response_locale,
        message_key or f"error.{code}",
        params,
        fallback=message or code,
    )
    if response_locale == "en-US" and any("\u4e00" <= char <= "\u9fff" for char in localized_message):
        localized_message = code.replace("_", " ").strip().capitalize()
    response = JSONResponse(
        status_code=status_code,
        content=public_api_error_response(
            code=code,
            message=localized_message,
            request_id=request_id,
            details=details,
        ),
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(ApiError)
def api_error_exception_handler(request: Request, exc: ApiError):
    if _is_json_api_request(request):
        return _api_error_json(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.detail,
            message_key=exc.message_key,
            params=exc.params,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_exception_handler(request: Request, exc: StarletteHTTPException):
    if not _is_json_api_request(request):
        return await http_exception_handler(request, exc)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _api_error_json(
        status_code=exc.status_code,
        code=_http_error_code(exc.status_code),
        message=message,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_exception_handler(request: Request, exc: RequestValidationError):
    if not _is_json_api_request(request):
        return await request_validation_exception_handler(request, exc)
    return _api_error_json(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details={"errors": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if not _is_json_api_request(request):
        raise exc
    logger.exception("Unhandled JSON API error")
    return _api_error_json(
        status_code=500,
        code="INTERNAL_ERROR",
        message="Internal server error",
    )


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)


@app.get("/openapi.json", include_in_schema=False)
async def localized_openapi(request: Request):
    return JSONResponse(build_openapi_schema(app, request.state.locale))


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(request: Request):
    locale = request.state.locale
    return get_swagger_ui_html(
        openapi_url=f"/openapi.json?lang={locale}",
        title=translate(locale, "openapi.docs.title"),
        swagger_js_url="/static/vendor/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/vendor/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/static/img/favicon.svg",
    )


@app.middleware("http")
async def _request_id_middleware(request, call_next):
    request_id = set_request_id(request.headers.get("X-Request-ID"))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.middleware("http")(i18n_http_middleware)


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
    locale, persist_locale_cookie = resolve_request_locale(request, user=user)
    request.state.locale = locale

    def localized_response(response):
        response.headers["Content-Language"] = locale
        if persist_locale_cookie:
            response.set_cookie("nb_locale", locale, path="/", samesite="lax")
        return response

    if skip_auth or allow_anonymous:
        return await call_next(request)

    if user is None:
        if _is_json_api_request(request):
            return localized_response(_api_error_json(
                status_code=401,
                code="UNAUTHORIZED",
                message="Unauthorized",
                locale=locale,
            ))
        nxt = quote(str(request.url.path) + (("?" + request.url.query) if request.url.query else ""))
        response = RedirectResponse(url=f"/login?next={nxt}", status_code=303)
        if request.cookies.get(settings.auth_cookie_name):
            response.delete_cookie(settings.auth_cookie_name, path="/")
        return localized_response(response)

    # 强制修改密码逻辑：如果密码已过期且当前不在修改密码页面，则强制跳转
    if user.password_expired and path != "/change-password":
        return localized_response(RedirectResponse(url="/change-password", status_code=303))

    if user.mfa_enabled and not user.mfa_secret and path not in {"/mfa-setup", "/logout", "/change-password"}:
        return localized_response(RedirectResponse(url="/mfa-setup", status_code=303))

    return await call_next(request)
