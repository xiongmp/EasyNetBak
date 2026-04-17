from __future__ import annotations

import csv
import io

from fastapi import APIRouter, BackgroundTasks, Form, Query, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, FileResponse
from fastapi_csrf_protect import CsrfProtect
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset
from app.db import session_scope
from app.models import AuditLog, BackupSchedule, WebshellRecord
from app.routers.common import _dt_local_str, _layout_context, _log_action, _require_permission, templates
from app.scheduler import run_cleanup, sync_scheduler_from_db
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.s3_service import test_s3_connection
from app.services.ftp_service import test_ftp_connection
from app.services.notification_service import send_email
from app.services.apikey import generate_api_key
from datetime import timedelta


router = APIRouter(tags=["系统设置 (System)"])

AUDIT_ACTION_MAP = {
    "LOGIN": "登录",
    "LOGOUT": "登出",
    "CREATE_DEVICE": "创建设备",
    "UPDATE_DEVICE": "更新设备",
    "DELETE_DEVICE": "删除设备",
    "TRIGGER_BACKUP": "触发备份",
    "TRIGGER_BACKUP_API": "API 触发备份",
    "BULK_BACKUP_API": "API 批量备份",
    "DELETE_BACKUP": "删除备份",
    "CREATE_CREDENTIAL": "创建凭据",
    "UPDATE_CREDENTIAL": "更新凭据",
    "DELETE_CREDENTIAL": "删除凭据",
    "CREATE_GROUP": "创建分组",
    "UPDATE_GROUP": "更新分组",
    "DELETE_GROUP": "删除分组",
    "CREATE_TEMPLATE": "创建模板",
    "UPDATE_TEMPLATE": "更新模板",
    "DELETE_TEMPLATE": "删除模板",
    "CREATE_SCHEDULE": "创建定时任务",
    "UPDATE_SCHEDULE": "更新定时任务",
    "DELETE_SCHEDULE": "删除定时任务",
    "TOGGLE_SCHEDULE": "启用/禁用定时任务",
    "TRIGGER_SCHEDULE_API": "API 手动触发定时任务",
    "UPDATE_SETTINGS": "更新系统设置",
    "UPDATE_STORAGE_SETTINGS": "更新存储设置",
    "UPDATE_DIFF_RULES": "更新Diff忽略规则",
    "UPDATE_NOTIFICATIONS": "更新通知设置",
    "OPEN_WEBSHELL": "打开 WebShell",
    "CLOSE_WEBSHELL": "关闭 WebShell",
    "CREATE_USER": "创建用户",
    "UPDATE_USER": "更新用户",
    "DELETE_USER": "删除用户",
    "RESET_RECOVERY_CODES": "重置恢复码",
    "USE_RECOVERY_CODE": "使用恢复码",
    "CREATE_ROLE": "创建角色",
    "UPDATE_ROLE": "更新角色",
    "DELETE_ROLE": "删除角色",
    "CHANGE_PASSWORD": "修改密码",
    "ENABLE_MFA": "启用 MFA",
}

AUDIT_RESOURCE_MAP = {
    "device": "设备",
    "credential": "凭据",
    "group": "分组",
    "template": "模板",
    "backup": "备份",
    "schedule": "定时任务",
    "settings": "系统设置",
    "storage_settings": "存储设置",
    "notifications": "通知设置",
    "user": "用户",
    "role": "角色",
}


@router.get("/audit-logs", summary="操作日志页面", description="查看系统操作审计日志")
def list_audit_logs(
    request: Request,
    q: str = Query(None),
    action: str = Query(None),
    resource_type: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    _require_permission(request, "audit_logs.view")
    offset = (page - 1) * limit
    with session_scope() as session:
        logs = crud.list_audit_logs(session, q=q, action=action, resource_type=resource_type, limit=limit, offset=offset)
        total = crud.count_audit_logs(session, q=q, action=action, resource_type=resource_type)
        all_actions = session.exec(select(AuditLog.action).distinct()).all()
        all_resource_types = session.exec(select(AuditLog.resource_type).distinct()).all()

    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/audit-logs?q={q or ''}&action={action or ''}&resource_type={resource_type or ''}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/audit-logs?q={q or ''}&action={action or ''}&resource_type={resource_type or ''}&limit={limit}&page="
         else:
             pagination_base = f"/audit-logs?q={q or ''}&action={action or ''}&resource_type={resource_type or ''}&page="

    return templates.TemplateResponse(
        request=request,
        name="audit_logs.html",
        context={
            **_layout_context(request=request, active="audit_logs"),
            "page_title": "操作日志",
            "logs": logs,
            "q": q or "",
            "action": action or "",
            "resource_type": resource_type or "",
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
            "all_actions": all_actions,
            "all_resource_types": all_resource_types,
            "action_map": AUDIT_ACTION_MAP,
            "resource_map": AUDIT_RESOURCE_MAP,
        },
    )


@router.get("/audit-logs/export.csv", summary="导出审计日志", description="导出审计日志为CSV文件")
def export_audit_logs(
    request: Request,
    q: str = Query(None),
    action: str = Query(None),
    resource_type: str = Query(None),
):
    _require_permission(request, "audit_logs.view")
    with session_scope() as session:
        logs = crud.list_audit_logs(session, q=q, action=action, resource_type=resource_type, limit=10000, offset=0)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间", "用户ID", "用户名", "操作", "资源类型", "资源ID", "详情", "IP地址"])

    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0))

    for log in logs:
        writer.writerow(
            [
                _dt_local_str(log.created_at, offset_minutes=offset_minutes),
                log.user_id or "",
                log.username or "",
                log.action,
                log.resource_type,
                log.resource_id or "",
                log.details or "",
                log.ip_address or "",
            ]
        )

    output.seek(0)
    content = "\ufeff" + output.getvalue()

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get("/login-logs", summary="登录日志页面", description="查看用户登录系统的历史记录")
def list_login_logs(
    request: Request,
    q: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    _require_permission(request, "login_logs.view")
    offset = (page - 1) * limit
    
    # status is now passed directly as string (success, fail, logout)
    status_filter = status if status in ["success", "fail", "logout"] else None

    with session_scope() as session:
        logs = crud.list_login_logs(session, q=q, status=status_filter, limit=limit, offset=offset)
        total = crud.count_login_logs(session, q=q, status=status_filter)

    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&limit={limit}&page="
         else:
             pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&page="

    return templates.TemplateResponse(
        request=request,
        name="login_logs.html",
        context={
            **_layout_context(request=request, active="login_logs"),
            "page_title": "登录日志",
            "logs": logs,
            "q": q or "",
            "status": status or "",
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )


@router.get("/login-logs/export.csv", summary="导出登录日志", description="导出登录日志为CSV文件")
def export_login_logs(
    request: Request,
    q: str = Query(None),
    status: str = Query(None),
):
    _require_permission(request, "login_logs.view")
    status_bool = None
    if status == "success":
        status_bool = True
    elif status == "fail":
        status_bool = False

    with session_scope() as session:
        logs = crud.list_login_logs(session, q=q, status=status_bool, limit=10000, offset=0)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间", "用户名", "状态", "IP地址", "浏览器/客户端", "失败原因"])

    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", 0))

    for log in logs:
        status_str = "成功" if log.status else "失败"
        writer.writerow(
            [
                _dt_local_str(log.created_at, offset_minutes=offset_minutes),
                log.username,
                status_str,
                log.ip_address or "",
                log.user_agent or "",
                log.fail_reason or "",
            ]
        )

    output.seek(0)
    content = "\ufeff" + output.getvalue()

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=login_logs.csv"},
    )


@router.get("/api-keys", summary="API Key 管理页面", description="查看和管理用于外部接入的 API Keys")
def api_keys_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_permission(request, "api_keys.view")
    with session_scope() as session:
        api_keys = crud.get_api_keys(session, limit=100)
    
    # Check if session middleware is installed properly
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
            "api_keys": api_keys,
            "new_key": new_key,
        }
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response

@router.post("/api-keys", summary="创建 API Key", description="生成新的 API Key")
def create_api_key(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    name: str = Form(...),
    expires_in_days: int = Form(0)
):
    csrf_protect.validate_csrf(request)
    user = _require_permission(request, "api_keys.create")
    
    plaintext_key, key_hash, prefix = generate_api_key()
    
    from app.models import ApiKey
    from datetime import datetime
    
    expires_at = None
    if expires_in_days > 0:
        expires_at = datetime.utcnow() + timedelta(days=expires_in_days)
        
    api_key = ApiKey(
        name=name.strip(),
        key_hash=key_hash,
        prefix=prefix,
        is_active=True,
        scopes="all",
        created_by=user.id,
        expires_at=expires_at
    )
    
    with session_scope() as session:
        crud.create_api_key(session, api_key=api_key)
        _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Created API Key: {name}")

    if "session" in request.scope:
        request.session["new_api_key"] = plaintext_key
        return RedirectResponse(url="/api-keys", status_code=303)
    else:
        # Fallback if no SessionMiddleware is configured
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        with session_scope() as session:
            api_keys = crud.get_api_keys(session, limit=100)
        resp = templates.TemplateResponse(
            request=request,
            name="api_keys.html",
            context={
                **_layout_context(request=request, active="api_keys"),
                "csrf_token": csrf_token,
                "api_keys": api_keys,
                "new_key": plaintext_key,
            }
        )
        csrf_protect.set_csrf_cookie(signed_token, resp)
        return resp

@router.post("/api-keys/{key_id}/revoke", summary="吊销 API Key", description="使 API Key 立即失效")
def revoke_api_key_endpoint(
    request: Request,
    key_id: int,
    csrf_protect: CsrfProtect = Depends()
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "api_keys.delete")
    
    with session_scope() as session:
        if crud.revoke_api_key(session, key_id):
            _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Revoked API Key: {key_id}")
            
    return RedirectResponse(url="/api-keys", status_code=303)

@router.post("/api-keys/{key_id}/delete", summary="删除 API Key", description="彻底删除 API Key")
def delete_api_key_endpoint(
    request: Request,
    key_id: int,
    csrf_protect: CsrfProtect = Depends()
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "api_keys.delete")
    
    with session_scope() as session:
        if crud.delete_api_key(session, key_id):
            _log_action(request, session, "UPDATE_SETTINGS", "settings", None, f"Deleted API Key: {key_id}")
            
    return RedirectResponse(url="/api-keys", status_code=303)

@router.get("/settings", summary="系统参数页面", description="查看系统全局参数配置")
def settings_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_permission(request, "settings.view")
    with session_scope() as session:
        timezone_str = crud.get_setting(session, key="timezone_offset")
        max_concurrent = crud.get_setting(session, key="max_concurrent_tasks")
        backup_max_retries = crud.get_setting(session, key="backup_max_retries")
        backup_retry_backoff = crud.get_setting(session, key="backup_retry_backoff")
        task_time_limit = crud.get_setting(session, key="task_time_limit")
        retention_days = crud.get_setting(session, key="backup_retention_days")
        webshell_retention_days = crud.get_setting(session, key="webshell_record_retention_days")
        audit_log_retention_days = crud.get_setting(session, key="audit_log_retention_days")
        login_log_retention_days = crud.get_setting(session, key="login_log_retention_days")

    timezone_offset = normalize_timezone_offset(timezone_str, default=settings.timezone_offset)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            **_layout_context(request=request, active="settings"),
            "csrf_token": csrf_token,
            "timezone_offset": timezone_offset,
            "max_concurrent_tasks": max_concurrent or "10",
            "backup_max_retries": backup_max_retries if backup_max_retries is not None else str(settings.celery.backup_max_retries),
            "backup_retry_backoff": backup_retry_backoff if backup_retry_backoff is not None else str(settings.celery.backup_retry_backoff_seconds),
            "task_time_limit": task_time_limit if (task_time_limit and task_time_limit != "0") else str(settings.celery.task_time_limit_seconds),
            "backup_retention_days": retention_days or "90",
            "webshell_record_retention_days": webshell_retention_days or "30",
            "audit_log_retention_days": audit_log_retention_days or "180",
            "login_log_retention_days": login_log_retention_days or "180",
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response



@router.post("/settings", summary="更新系统参数", description="修改系统全局参数")
def update_settings(
    request: Request,
    background: BackgroundTasks,
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
    tz = normalize_timezone_offset(timezone_offset, default=settings.timezone_offset)
    try:
        val = int(max_concurrent_tasks)
        if val < 1:
            val = 1
        if val > 100:
            val = 100
        max_concurrent_tasks = str(val)
    except (ValueError, TypeError):
        max_concurrent_tasks = "10"
    
    try:
        val = int(backup_max_retries)
        if val < 0: val = 0
        if val > 10: val = 10
        backup_max_retries = str(val)
    except:
        backup_max_retries = "3"

    try:
        val = int(backup_retry_backoff)
        if val < 1: val = 1
        if val > 3600: val = 3600
        backup_retry_backoff = str(val)
    except:
        backup_retry_backoff = "10"

    try:
        val = int(task_time_limit)
        if val < 0: val = 0 # 0 means no limit
        if val > 3600: val = 3600
        task_time_limit = str(val)
    except:
        task_time_limit = "300"

    try:
        val = int(backup_retention_days)
        if val < 1:
            val = 1
        backup_retention_days = str(val)
    except (ValueError, TypeError):
        backup_retention_days = "90"

    try:
        val = int(webshell_record_retention_days)
        if val < 1:
            val = 1
        webshell_record_retention_days = str(val)
    except (ValueError, TypeError):
        webshell_record_retention_days = "30"

    try:
        val = int(audit_log_retention_days)
        if val < 1:
            val = 1
        audit_log_retention_days = str(val)
    except (ValueError, TypeError):
        audit_log_retention_days = "180"

    try:
        val = int(login_log_retention_days)
        if val < 1:
            val = 1
        login_log_retention_days = str(val)
    except (ValueError, TypeError):
        login_log_retention_days = "180"

    with session_scope() as session:
        crud.set_setting(session, key="timezone_offset", value=tz)
        crud.set_setting(session, key="max_concurrent_tasks", value=max_concurrent_tasks)
        crud.set_setting(session, key="backup_max_retries", value=backup_max_retries)
        crud.set_setting(session, key="backup_retry_backoff", value=backup_retry_backoff)
        crud.set_setting(session, key="task_time_limit", value=task_time_limit)
        crud.set_setting(session, key="backup_retention_days", value=backup_retention_days)
        crud.set_setting(session, key="webshell_record_retention_days", value=webshell_record_retention_days)
        crud.set_setting(session, key="audit_log_retention_days", value=audit_log_retention_days)
        crud.set_setting(session, key="login_log_retention_days", value=login_log_retention_days)

        _log_action(
            request,
            session,
            "UPDATE_SETTINGS",
            "settings",
            None,
            f"TZ: {tz}, MaxConcurrent: {max_concurrent_tasks}, Backup Retention: {backup_retention_days}, Webshell Retention: {webshell_record_retention_days}, Audit Retention: {audit_log_retention_days}, Login Retention: {login_log_retention_days}",
        )

    background.add_task(run_cleanup)

    return RedirectResponse(url="/settings?msg=已保存", status_code=303)



@router.post("/settings/test-s3", summary="测试S3连通性", description="验证S3存储配置是否可用")
def api_test_s3(
    request: Request,
    s3_endpoint: str = Form(""),
    s3_region: str = Form(""),
    s3_access_key: str = Form(""),
    s3_secret_key: str = Form(""),
    s3_bucket: str = Form(""),
):
    _require_permission(request, "storage_settings.update")
    
    # 如果 secret_key 为空或为纯星号掩码，尝试从数据库获取
    if not s3_secret_key or (set(s3_secret_key) == {'*'}):
        with session_scope() as session:
            s3_secret_key = decrypt_secret(crud.get_setting(session, key="s3_secret_key")) or ""
    
    # 如果 access_key 为空或为纯星号掩码，尝试从数据库获取
    if not s3_access_key or (set(s3_access_key) == {'*'}):
        with session_scope() as session:
            s3_access_key = decrypt_secret(crud.get_setting(session, key="s3_access_key")) or ""

    success, message = test_s3_connection(
        endpoint=s3_endpoint.strip(),
        access_key=s3_access_key.strip(),
        secret_key=s3_secret_key.strip(),
        bucket=s3_bucket.strip(),
        region=s3_region.strip()
    )
    
    return {"success": success, "message": message}


@router.post("/settings/test-ftp", summary="测试FTP连通性", description="验证FTP存储配置是否可用")
def api_test_ftp(
    request: Request,
    ftp_host: str = Form(""),
    ftp_port: str = Form("21"),
    ftp_username: str = Form(""),
    ftp_password: str = Form(""),
    ftp_base_dir: str = Form(""),
    ftp_passive: str = Form("1"),
    ftp_timeout: str = Form("15"),
):
    _require_permission(request, "storage_settings.update")

    if not ftp_password or (set(ftp_password) == {'*'}):
        with session_scope() as session:
            ftp_password = decrypt_secret(crud.get_setting(session, key="ftp_password")) or ""

    success, message = test_ftp_connection(
        host=ftp_host.strip(),
        port=ftp_port.strip(),
        username=ftp_username.strip(),
        password=ftp_password.strip(),
        base_dir=ftp_base_dir.strip(),
        passive=ftp_passive.strip(),
        timeout=ftp_timeout.strip(),
    )

    return {"success": success, "message": message}


@router.get("/notifications", summary="通知设置页面", description="查看系统消息通知配置")
def notifications_page(request: Request):
    _require_permission(request, "notifications.view")
    with session_scope() as session:
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
        with session_scope() as session:
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
    with session_scope() as session:
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

    return RedirectResponse(url="/notifications?msg=已保存", status_code=303)


@router.get("/storage-settings", summary="存储配置页面", description="查看远程存储设置")
def storage_settings_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_permission(request, "storage_settings.view")
    with session_scope() as session:
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
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/storage-settings", summary="更新存储配置", description="修改存储参数")
def update_storage_settings(
    request: Request,
    background: BackgroundTasks,
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

    with session_scope() as session:
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

        _log_action(
            request,
            session,
            "UPDATE_STORAGE_SETTINGS",
            "storage_settings",
            None,
            f"Update Storage Settings: S3Enabled: {s3_enabled}, FTPEnabled: {ftp_enabled}",
        )

    return RedirectResponse(url="/storage-settings?msg=已保存", status_code=303)


@router.post("/settings/schedule", summary="更新调度配置", description="修改任务调度相关参数")
def legacy_update_schedule(
    request: Request,
    schedule_enabled: str = Form("0"),
    backup_crontab: str = Form("0 2 * * *"),
    timezone_offset: str = Form(settings.timezone_offset),
):
    _require_permission(request, "settings.update")
    enabled = schedule_enabled in {"1", "true", "True", "yes", "YES", "on"}
    crontab = (backup_crontab or "").strip() or "0 2 * * *"
    tz = normalize_timezone_offset(timezone_offset, default=settings.timezone_offset)
    with session_scope() as session:
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
    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=已保存", status_code=303)


@router.get("/login-logs", summary="登录日志页面", description="查看用户登录系统的历史记录")
def list_login_logs(
    request: Request,
    q: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    _require_permission(request, "login_logs.view")
    offset = (page - 1) * limit
    status_bool = None
    if status == "success":
        status_bool = True
    elif status == "fail":
        status_bool = False

    with session_scope() as session:
        logs = crud.list_login_logs(session, q=q, status=status_bool, limit=limit, offset=offset)
        total = crud.count_login_logs(session, q=q, status=status_bool)

    pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&limit={limit}&page="
         else:
             pagination_base = f"/login-logs?q={q or ''}&status={status or ''}&page="

    return templates.TemplateResponse(
        request=request,
        name="login_logs.html",
        context={
            **_layout_context(request=request, active="login_logs"),
            "page_title": "登录日志",
            "logs": logs,
            "q": q or "",
            "status": status or "",
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": max(1, (total + limit - 1) // limit),
            },
            "pagination_base": pagination_base,
        },
    )


@router.get("/login-logs/export.csv", summary="导出登录日志", description="导出登录日志为CSV文件")
def export_login_logs_csv(
    request: Request,
    q: str = Query(None),
    status: str = Query(None),
):
    _require_permission(request, "login_logs.view")
    
    # status is now passed directly as string (success, fail, logout)
    status_filter = status if status in ["success", "fail", "logout"] else None

    with session_scope() as session:
        # Export all matching logs (no limit)
        logs = crud.list_login_logs(session, q=q, status=status_filter, limit=100000, offset=0)
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "时间", "用户名", "状态", "IP地址", "浏览器/客户端", "失败原因"])
        
        for log in logs:
            status_text = "未知"
            if log.status == "success":
                status_text = "登录成功"
            elif log.status == "fail":
                status_text = "登录失败"
            elif log.status == "logout":
                status_text = "登出"
                
            writer.writerow([
                log.id,
                _dt_local_str(log.created_at, offset_minutes=getattr(request.state, "tz_offset_minutes", 0)),
                log.username,
                status_text,
                log.ip_address or "",
                log.user_agent or "",
                log.fail_reason or "",
            ])
            
        output.seek(0)
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=login_logs.csv"}
        )

import os

@router.get("/webshell-records", summary="WebShell录像页面", description="查看WebShell会话录像")
def list_webshell_records(
    request: Request,
    q: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    _require_permission(request, "audit_logs.view")
    _require_permission(request, "webshell_records.view")
    offset = (page - 1) * limit
    with session_scope() as session:
        records = crud.list_webshell_records(session, q=q, limit=limit, offset=offset)
        total = crud.count_webshell_records(session, q=q)

    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/webshell-records?q={q or ''}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/webshell-records?q={q or ''}&limit={limit}&page="
         else:
             pagination_base = f"/webshell-records?q={q or ''}&page="

    return templates.TemplateResponse(
        request=request,
        name="webshell_records.html",
        context={
            **_layout_context(request=request, active="webshell_records"),
            "page_title": "Webshell 回放",
            "records": records,
            "q": q or "",
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )

@router.get("/webshell-records/{record_id}/cast", summary="回放WebShell", description="播放指定的WebShell录像文件")
def get_webshell_cast(request: Request, record_id: int):
    _require_permission(request, "audit_logs.view")
    _require_permission(request, "webshell_records.view")
    with session_scope() as session:
        record = session.get(WebshellRecord, record_id)
        if not record or not os.path.exists(record.file_path):
            raise HTTPException(status_code=404, detail="Recording not found")
        return FileResponse(record.file_path, media_type="application/json")
