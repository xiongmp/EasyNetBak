from __future__ import annotations

import logging
import os
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi_csrf_protect import CsrfProtect
from sqlmodel import Session, select

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset
from app.db import get_session
from app.i18n import translate
from app.models import BackupSchedule, WebshellRecord
from app.routers.support import _log_action, _require_any_permission, _require_permission
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import AuditLogListQueryInput, BaseListQueryInput, LoginLogListQueryInput, SearchListQueryInput
from app.scheduler import run_cleanup, sync_scheduler_from_db
from app.services import api_key_management_service, audit_service, pagination_service, settings_service
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.errors import ServiceError
from app.services.s3_service import test_s3_connection
from app.services.ftp_service import test_ftp_connection
from app.services.notification_service import send_email


router = APIRouter(tags=["系统设置 (System)"])
logger = logging.getLogger(__name__)


@router.get("/audit-logs", summary="操作日志页面", description="查看系统操作审计日志")
def list_audit_logs(
    request: Request,
    session: Session = Depends(get_session),
):
    _require_permission(request, "audit_logs.view")
    list_query = AuditLogListQueryInput.from_query_params(request.query_params)
    payload = audit_service.get_audit_logs_page_payload(
        session,
        q=list_query.q,
        action=list_query.action,
        resource_type=list_query.resource_type,
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )

    return templates.TemplateResponse(
        request=request,
        name="audit_logs.html",
        context={
            **_layout_context(request=request, active="audit_logs"),
            "page_title": translate(request.state.locale, "nav.audit_logs"),
            **payload,
        },
    )


@router.get("/audit-logs/export.csv", summary="导出审计日志", description="导出审计日志为CSV文件")
def export_audit_logs(
    request: Request,
    session: Session = Depends(get_session),
    q: str = Query(None),
    action: str = Query(None),
    resource_type: str = Query(None),
):
    _require_permission(request, "audit_logs.view")
    return audit_service.export_audit_logs_csv(
        session,
        q=q,
        action=action,
        resource_type=resource_type,
        offset_minutes=int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0)),
    )


@router.get("/login-logs", summary="登录日志页面", description="查看用户登录系统的历史记录")
def list_login_logs(
    request: Request,
    session: Session = Depends(get_session),
):
    _require_permission(request, "login_logs.view")
    list_query = LoginLogListQueryInput.from_query_params(request.query_params)
    payload = audit_service.get_login_logs_page_payload(
        session,
        q=list_query.q,
        status=list_query.status,
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )

    return templates.TemplateResponse(
        request=request,
        name="login_logs.html",
        context={
            **_layout_context(request=request, active="login_logs"),
            "page_title": translate(request.state.locale, "nav.login_logs"),
            **payload,
        },
    )


@router.get("/login-logs/export.csv", summary="导出登录日志", description="导出登录日志为CSV文件")
def export_login_logs(
    request: Request,
    session: Session = Depends(get_session),
    q: str = Query(None),
    status: str = Query(None),
):
    _require_permission(request, "login_logs.view")
    return audit_service.export_login_logs_csv(
        session,
        q=q,
        status=status,
        offset_minutes=int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0)),
    )


@router.get("/api-keys", summary="API Key 管理页面", description="查看和管理用于外部接入的 API Keys")
def api_keys_page(request: Request, csrf_protect: CsrfProtect = Depends(), session: Session = Depends(get_session)):
    _require_any_permission(request, ["api_keys.view", "api_keys.create", "api_keys.delete"])
    list_query = BaseListQueryInput.from_query_params(request.query_params)
    payload = api_key_management_service.get_api_keys_page_payload(
        session,
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )

    new_key = None
    if "session" in request.scope:
        new_key = request.session.pop("new_api_key", None)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="api_keys.html",
        context={
            **_layout_context(request=request, active="api_keys"),
            "csrf_token": csrf_token,
            **payload,
            "new_key": new_key,
        }
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/api-keys", summary="创建 API Key", description="生成新的 API Key")
def create_api_key(
    request: Request,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends(),
    name: str = Form(...),
    expires_in_days: int = Form(0)
):
    csrf_protect.validate_csrf(request)
    user = _require_permission(request, "api_keys.create")

    try:
        _, plaintext_key = api_key_management_service.create_api_key(
            session,
            name=name,
            created_by=user.id,
            expires_in_days=expires_in_days,
        )
    except ServiceError as exc:
        return RedirectResponse(url=f"/api-keys?err={quote(exc.message)}", status_code=303)
    _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Created API Key: {name.strip()}")

    if "session" in request.scope:
        request.session["new_api_key"] = plaintext_key
        return RedirectResponse(url="/api-keys", status_code=303)
    else:
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        payload = api_key_management_service.get_api_keys_page_payload(
            session,
            page=1,
            limit=10,
            limit_in_query=False,
        )
        resp = templates.TemplateResponse(
            request=request,
            name="api_keys.html",
            context={
                **_layout_context(request=request, active="api_keys"),
                "csrf_token": csrf_token,
                **payload,
                "new_key": plaintext_key,
            }
        )
        csrf_protect.set_csrf_cookie(signed_token, resp)
        return resp

@router.post("/api-keys/{key_id}/revoke", summary="吊销 API Key", description="使 API Key 立即失效")
def revoke_api_key_endpoint(
    request: Request,
    key_id: int,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends()
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "api_keys.delete")
    
    if crud.revoke_api_key(session, key_id):
        _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Revoked API Key: {key_id}")
            
    return RedirectResponse(url="/api-keys", status_code=303)

@router.post("/api-keys/{key_id}/delete", summary="删除 API Key", description="彻底删除 API Key")
def delete_api_key_endpoint(
    request: Request,
    key_id: int,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends()
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "api_keys.delete")
    
    if crud.delete_api_key(session, key_id):
        _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Deleted API Key: {key_id}")
            
    return RedirectResponse(url="/api-keys", status_code=303)

@router.get("/settings", summary="系统参数页面", description="查看系统全局参数配置")
def settings_page(request: Request, csrf_protect: CsrfProtect = Depends(), session: Session = Depends(get_session)):
    _require_any_permission(request, ["settings.view", "settings.update"])
    settings_payload = settings_service.get_system_settings_payload(session)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            **_layout_context(request=request, active="settings"),
            "csrf_token": csrf_token,
            **settings_payload.as_dict(),
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response



@router.post("/settings", summary="更新系统参数", description="修改系统全局参数")
def update_settings(
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends(),
    timezone_offset: str = Form(settings.timezone_offset),
    max_concurrent_tasks: str = Form("10"),
    backup_max_retries: str = Form("3"),
    backup_retry_backoff: str = Form("10"),
    task_time_limit: str = Form("300"),
    backup_retention_days: str = Form("90"),
    webshell_record_retention_days: str = Form("30"),
    audit_log_retention_days: str = Form("180"),
    login_log_retention_days: str = Form("180"),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "settings.update")
    settings_payload = settings_service.save_system_settings(
        session,
        timezone_offset=timezone_offset,
        max_concurrent_tasks=max_concurrent_tasks,
        backup_max_retries=backup_max_retries,
        backup_retry_backoff=backup_retry_backoff,
        task_time_limit=task_time_limit,
        backup_retention_days=backup_retention_days,
        webshell_record_retention_days=webshell_record_retention_days,
        audit_log_retention_days=audit_log_retention_days,
        login_log_retention_days=login_log_retention_days,
    )

    _log_action(
        request,
        session,
        "UPDATE_SETTINGS",
        "settings",
        None,
        settings_service.build_system_settings_audit_details(settings_payload),
    )

    background.add_task(run_cleanup)

    return RedirectResponse(url="/settings?msg=message.saved", status_code=303)



@router.post("/settings/test-s3", summary="测试S3连通性", description="验证S3存储配置是否可用")
def api_test_s3(
    request: Request,
    session: Session = Depends(get_session),
    s3_endpoint: str = Form(""),
    s3_region: str = Form(""),
    s3_access_key: str = Form(""),
    s3_secret_key: str = Form(""),
    s3_bucket: str = Form(""),
):
    _require_permission(request, "storage_settings.update")
    
    # 如果 secret_key 为空或为纯星号掩码，尝试从数据库获取
    if not s3_secret_key or (set(s3_secret_key) == {'*'}):
        s3_secret_key = decrypt_secret(crud.get_setting(session, key="s3_secret_key")) or ""
    
    # 如果 access_key 为空或为纯星号掩码，尝试从数据库获取
    if not s3_access_key or (set(s3_access_key) == {'*'}):
        s3_access_key = decrypt_secret(crud.get_setting(session, key="s3_access_key")) or ""

    success, message = test_s3_connection(
        endpoint=s3_endpoint.strip(),
        access_key=s3_access_key.strip(),
        secret_key=s3_secret_key.strip(),
        bucket=s3_bucket.strip(),
        region=s3_region.strip(),
        locale=request.state.locale,
    )
    
    return {"success": success, "message": message}


@router.post("/settings/test-ftp", summary="测试FTP连通性", description="验证FTP存储配置是否可用")
def api_test_ftp(
    request: Request,
    session: Session = Depends(get_session),
    ftp_host: str = Form(""),
    ftp_port: str = Form("21"),
    ftp_username: str = Form(""),
    ftp_password: str = Form(""),
    ftp_base_dir: str = Form(""),
    ftp_passive: str = Form("1"),
    ftp_timeout: str = Form("15"),
    ftp_encoding: str = Form("utf-8"),
):
    _require_permission(request, "storage_settings.update")

    if not ftp_password or (set(ftp_password) == {'*'}):
        ftp_password = decrypt_secret(crud.get_setting(session, key="ftp_password")) or ""

    success, message = test_ftp_connection(
        host=ftp_host.strip(),
        port=ftp_port.strip(),
        username=ftp_username.strip(),
        password=ftp_password.strip(),
        base_dir=ftp_base_dir.strip(),
        passive=ftp_passive.strip(),
        timeout=ftp_timeout.strip(),
        encoding=ftp_encoding.strip(),
        locale=request.state.locale,
    )

    return {"success": success, "message": message}


@router.get("/notifications", summary="通知设置页面", description="查看系统消息通知配置")
def notifications_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(request, ["notifications.view", "notifications.update"])
    smtp_host = crud.get_setting(session, key="smtp_host") or ""
    smtp_port = crud.get_setting(session, key="smtp_port") or "25"
    smtp_user = crud.get_setting(session, key="smtp_user") or ""
    smtp_pass = decrypt_secret(crud.get_setting(session, key="smtp_pass")) or ""
    smtp_from = crud.get_setting(session, key="smtp_from") or ""
    smtp_to = crud.get_setting(session, key="smtp_to") or ""
    alert_on_fail = crud.get_setting(session, key="alert_on_fail") or "0"
    alert_on_config_change = crud.get_setting(session, key="alert_on_config_change") or "0"
    always_send_summary = crud.get_setting(session, key="always_send_summary") or "0"

    # 对 SMTP 密码进行等长掩码处理
    display_smtp_pass = "*" * len(smtp_pass) if smtp_pass else ""

    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={
            **_layout_context(request=request, active="notifications"),
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_pass": display_smtp_pass,
            "smtp_from": smtp_from,
            "smtp_to": smtp_to,
            "alert_on_fail": alert_on_fail,
            "alert_on_config_change": alert_on_config_change,
            "always_send_summary": always_send_summary,
        },
    )


@router.post("/notifications/test", summary="测试通知设置", description="发送测试请求以验证通知配置")
def test_notifications(
    request: Request,
    session: Session = Depends(get_session),
    smtp_host: str = Form(""),
    smtp_port: str = Form("25"),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
):
    _require_permission(request, "notifications.update")

    # 如果 smtp_pass 为空或为纯星号掩码，尝试从数据库获取
    if not smtp_pass or (set(smtp_pass) == {'*'}):
        smtp_pass = decrypt_secret(crud.get_setting(session, key="smtp_pass")) or ""

    config = {
        "smtp_host": smtp_host.strip(),
        "smtp_port": smtp_port.strip(),
        "smtp_user": smtp_user.strip(),
        "smtp_pass": smtp_pass.strip(),
        "smtp_from": smtp_from.strip(),
        "smtp_to": smtp_to.strip(),
    }

    try:
        success = send_email(
            subject="【测试】网络设备备份系统 - 测试邮件",
            content="这是一封来自网络设备备份系统的测试邮件，证明您的 SMTP 配置工作正常。",
            smtp_config=config,
        )
        if success:
            return {"success": True, "message": "测试邮件已发送，请检查收件箱。"}
        return {"success": False, "message": "邮件发送失败，请检查配置。"}
    except Exception as exc:
        return {"success": False, "message": f"发送出错: {str(exc)}"}


@router.post("/notifications", summary="更新通知设置", description="修改通知参数")
def update_notifications(
    request: Request,
    session: Session = Depends(get_session),
    smtp_host: str = Form(""),
    smtp_port: str = Form("25"),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
    alert_on_fail: str = Form("0"),
    alert_on_config_change: str = Form("0"),
    always_send_summary: str = Form("0"),
):
    _require_permission(request, "notifications.update")
    crud.set_setting(session, key="smtp_host", value=smtp_host.strip())
    crud.set_setting(session, key="smtp_port", value=smtp_port.strip())
    crud.set_setting(session, key="smtp_user", value=smtp_user.strip())

    # 只有当用户输入的值不是全星号（掩码）且不为空时，才更新
    if smtp_pass and not (set(smtp_pass) == {'*'}):
        crud.set_setting(session, key="smtp_pass", value=encrypt_secret(smtp_pass.strip()))

    crud.set_setting(session, key="smtp_from", value=smtp_from.strip())
    crud.set_setting(session, key="smtp_to", value=smtp_to.strip())
    crud.set_setting(session, key="alert_on_fail", value="1" if alert_on_fail in {"1", "on"} else "0")
    crud.set_setting(
        session, key="alert_on_config_change", value="1" if alert_on_config_change in {"1", "on"} else "0"
    )
    crud.set_setting(session, key="always_send_summary", value="1" if always_send_summary in {"1", "on"} else "0")
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "settings", None, "Updated notification settings")

    return RedirectResponse(url="/notifications?msg=message.saved", status_code=303)


@router.get("/storage-settings", summary="存储配置页面", description="查看远程存储设置")
def storage_settings_page(request: Request, csrf_protect: CsrfProtect = Depends(), session: Session = Depends(get_session)):
    _require_any_permission(request, ["storage_settings.view", "storage_settings.update"])
    s3_enabled = crud.get_setting(session, key="s3_enabled") or "0"
    s3_endpoint = crud.get_setting(session, key="s3_endpoint") or ""
    s3_access_key = decrypt_secret(crud.get_setting(session, key="s3_access_key")) or ""
    s3_secret_key = decrypt_secret(crud.get_setting(session, key="s3_secret_key")) or ""
    s3_bucket = crud.get_setting(session, key="s3_bucket") or ""
    s3_region = crud.get_setting(session, key="s3_region") or ""
    s3_prefix = crud.get_setting(session, key="s3_prefix") or "backups"
    ftp_enabled = crud.get_setting(session, key="ftp_enabled") or "0"
    ftp_host = crud.get_setting(session, key="ftp_host") or ""
    ftp_port = crud.get_setting(session, key="ftp_port") or "21"
    ftp_username = crud.get_setting(session, key="ftp_username") or ""
    ftp_password = decrypt_secret(crud.get_setting(session, key="ftp_password")) or ""
    ftp_base_dir = crud.get_setting(session, key="ftp_base_dir") or ""
    ftp_passive = crud.get_setting(session, key="ftp_passive") or "1"
    ftp_timeout = crud.get_setting(session, key="ftp_timeout") or "15"
    ftp_encoding = crud.get_setting(session, key="ftp_encoding") or "utf-8"

    display_access_key = "*" * len(s3_access_key) if s3_access_key else ""
    display_secret_key = "*" * len(s3_secret_key) if s3_secret_key else ""
    display_ftp_password = "*" * len(ftp_password) if ftp_password else ""

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="storage_settings.html",
        context={
            **_layout_context(request=request, active="storage_settings"),
            "csrf_token": csrf_token,
            "s3_enabled": s3_enabled,
            "s3_endpoint": s3_endpoint,
            "s3_access_key": display_access_key,
            "s3_secret_key": display_secret_key,
            "s3_bucket": s3_bucket,
            "s3_region": s3_region,
            "s3_prefix": s3_prefix,
            "ftp_enabled": ftp_enabled,
            "ftp_host": ftp_host,
            "ftp_port": ftp_port,
            "ftp_username": ftp_username,
            "ftp_password": display_ftp_password,
            "ftp_base_dir": ftp_base_dir,
            "ftp_passive": ftp_passive,
            "ftp_timeout": ftp_timeout,
            "ftp_encoding": ftp_encoding,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/storage-settings", summary="更新存储配置", description="修改存储参数")
def update_storage_settings(
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends(),
    s3_enabled: str = Form("0"),
    s3_endpoint: str = Form(""),
    s3_access_key: str = Form(""),
    s3_secret_key: str = Form(""),
    s3_bucket: str = Form(""),
    s3_region: str = Form(""),
    s3_prefix: str = Form("backups"),
    ftp_enabled: str = Form("0"),
    ftp_host: str = Form(""),
    ftp_port: str = Form("21"),
    ftp_username: str = Form(""),
    ftp_password: str = Form(""),
    ftp_base_dir: str = Form(""),
    ftp_passive: str = Form("1"),
    ftp_timeout: str = Form("15"),
    ftp_encoding: str = Form("utf-8"),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "storage_settings.update")

    try:
        val = int(ftp_port)
        if val < 1: val = 1
        if val > 65535: val = 65535
        ftp_port = str(val)
    except (ValueError, TypeError):
        ftp_port = "21"

    try:
        val = int(ftp_timeout)
        if val < 1: val = 1
        if val > 300: val = 300
        ftp_timeout = str(val)
    except (ValueError, TypeError):
        ftp_timeout = "15"

    ftp_encoding = ftp_encoding.strip().lower()
    if ftp_encoding not in {"utf-8", "gbk", "latin-1"}:
        ftp_encoding = "utf-8"

    crud.set_setting(session, key="s3_enabled", value="1" if s3_enabled in {"1", "on"} else "0")
    crud.set_setting(session, key="s3_endpoint", value=s3_endpoint.strip())

    if s3_access_key and not (set(s3_access_key) == {'*'}):
        crud.set_setting(session, key="s3_access_key", value=encrypt_secret(s3_access_key.strip()))
    if s3_secret_key and not (set(s3_secret_key) == {'*'}):
        crud.set_setting(session, key="s3_secret_key", value=encrypt_secret(s3_secret_key.strip()))

    crud.set_setting(session, key="s3_bucket", value=s3_bucket.strip())
    crud.set_setting(session, key="s3_region", value=s3_region.strip())
    crud.set_setting(session, key="s3_prefix", value=s3_prefix.strip())
    crud.set_setting(session, key="ftp_enabled", value="1" if ftp_enabled in {"1", "on"} else "0")
    crud.set_setting(session, key="ftp_host", value=ftp_host.strip())
    crud.set_setting(session, key="ftp_port", value=ftp_port.strip())
    crud.set_setting(session, key="ftp_username", value=ftp_username.strip())
    if ftp_password and not (set(ftp_password) == {'*'}):
        crud.set_setting(session, key="ftp_password", value=encrypt_secret(ftp_password.strip()))
    crud.set_setting(session, key="ftp_base_dir", value=ftp_base_dir.strip())
    crud.set_setting(session, key="ftp_passive", value="1" if ftp_passive in {"1", "on"} else "0")
    crud.set_setting(session, key="ftp_timeout", value=ftp_timeout.strip())
    crud.set_setting(session, key="ftp_encoding", value=ftp_encoding)

    _log_action(
        request,
        session,
        "UPDATE_STORAGE_SETTINGS",
        "storage_settings",
        None,
        f"Update Storage Settings: S3Enabled: {s3_enabled}, FTPEnabled: {ftp_enabled}",
    )

    return RedirectResponse(url="/storage-settings?msg=message.saved", status_code=303)


@router.post("/settings/schedule", summary="更新调度配置", description="修改任务调度相关参数")
def legacy_update_schedule(
    request: Request,
    session: Session = Depends(get_session),
    schedule_enabled: str = Form("0"),
    backup_crontab: str = Form("0 2 * * *"),
    timezone_offset: str = Form(settings.timezone_offset),
):
    # 废弃兼容入口：当前界面已切换到“定时任务”多任务管理页。
    # 这里暂时保留给旧版本调用方使用，待确认无人访问后再删除。
    _require_permission(request, "settings.update")
    logger.warning(
        "Deprecated endpoint /settings/schedule was used by user=%s from ip=%s",
        getattr(getattr(request, "state", None), "user", None).username
        if getattr(getattr(request, "state", None), "user", None) is not None
        else "anonymous",
        getattr(getattr(request, "client", None), "host", "") or "",
    )
    enabled = schedule_enabled in {"1", "true", "True", "yes", "YES", "on"}
    crontab = (backup_crontab or "").strip() or "0 2 * * *"
    tz = normalize_timezone_offset(timezone_offset, default=settings.timezone_offset)
    crud.set_setting(session, key="timezone_offset", value=tz)
    items = crud.list_schedules(session)
    default_item = next((x for x in items if (x.name or "").strip() == "默认定时备份"), None)
    if default_item is None:
        crud.create_schedule(
            session,
            schedule=BackupSchedule(name="默认定时备份", crontab=crontab, enabled=enabled, targets="all"),
        )
    elif default_item.id:
        crud.update_schedule(
            session,
            int(default_item.id),
            name=default_item.name,
            crontab=crontab,
            enabled=enabled,
            targets=default_item.targets,
        )
    else:
        crud.create_schedule(
            session,
            schedule=BackupSchedule(
                name="默认定时备份",
                crontab=crontab,
                enabled=enabled,
                targets=default_item.targets or "all",
            ),
        )
    session.commit()
    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=message.saved", status_code=303)

@router.get("/webshell-records", summary="WebShell录像页面", description="查看WebShell会话录像")
def list_webshell_records(
    request: Request,
    session: Session = Depends(get_session),
):
    _require_permission(request, "audit_logs.view")
    _require_permission(request, "webshell_records.view")
    list_query = SearchListQueryInput.from_query_params(request.query_params)
    pagination_params = pagination_service.normalize_pagination_params(
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )
    records = crud.list_webshell_records(
        session,
        q=list_query.q,
        limit=pagination_params.limit,
        offset=pagination_params.offset,
    )
    total = crud.count_webshell_records(session, q=list_query.q)

    pagination = pagination_service.build_pagination_data(
        page=pagination_params.page,
        limit=pagination_params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/webshell-records",
        params={"q": list_query.q or ""},
        limit=pagination.limit,
        limit_explicit=pagination_params.limit_explicit,
    )

    return templates.TemplateResponse(
        request=request,
        name="webshell_records.html",
        context={
            **_layout_context(request=request, active="webshell_records"),
            "page_title": translate(request.state.locale, "nav.webshell_records"),
            "records": records,
            "q": list_query.q or "",
            "pagination": pagination.as_dict(),
            "pagination_base": pagination_base,
        },
    )

@router.get("/webshell-records/{record_id}/cast", summary="回放WebShell", description="播放指定的WebShell录像文件")
def get_webshell_cast(request: Request, record_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "audit_logs.view")
    _require_permission(request, "webshell_records.view")
    record = session.get(WebshellRecord, record_id)
    if not record or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(record.file_path, media_type="application/json")
