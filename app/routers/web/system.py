from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi_csrf_protect import CsrfProtect
from sqlmodel import Session, select

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset, parse_timezone_offset_to_minutes
from app.db import get_session
from app.i18n import translate
from app.models import BackupSchedule, WebshellRecord
from app.routers.support import _log_action, _require_any_permission, _require_permission
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import AuditLogListQueryInput, BaseListQueryInput, LoginLogListQueryInput, SearchListQueryInput
from app.scheduler import run_cleanup, sync_scheduler_from_db
from app.services import (
    api_key_management_service,
    audit_service,
    notification_routing_service,
    pagination_service,
    settings_service,
)
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.errors import ServiceError
from app.services.s3_service import test_s3_connection
from app.services.ftp_service import test_ftp_connection


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
def notifications_page(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
):
    _require_any_permission(request, ["notifications.view", "notifications.update"])
    notification_routing_service.ensure_builtin_defaults(session)

    locale = request.state.locale
    channel_rows = [
        notification_routing_service.serialize_channel(item, locale=locale)
        for item in notification_routing_service.list_channels(session)
    ]
    template_rows = [
        notification_routing_service.serialize_template(item, locale=locale)
        for item in notification_routing_service.list_templates(session)
    ]
    policy_rows = [
        notification_routing_service.serialize_policy(item, locale=locale)
        for item in notification_routing_service.list_policies(session)
    ]
    groups = crud.list_groups(session)
    platforms = sorted({str(item.platform).strip() for item in crud.list_devices(session) if str(item.platform or "").strip()})
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    list_query = BaseListQueryInput.from_query_params(request.query_params, default_limit=10)
    delivery_q = str(request.query_params.get("delivery_q") or "").strip()[:255]
    delivery_status = str(request.query_params.get("delivery_status") or "").strip()
    if delivery_status not in {"pending", "sending", "sent", "retrying", "failed"}:
        delivery_status = ""
    delivery_event = str(request.query_params.get("delivery_event") or "").strip()
    if delivery_event not in notification_routing_service.EVENT_TYPES:
        delivery_event = ""
    try:
        delivery_channel_id = int(request.query_params.get("delivery_channel") or 0) or None
    except (TypeError, ValueError):
        delivery_channel_id = None
    delivery_from = str(request.query_params.get("delivery_from") or "").strip()
    delivery_to = str(request.query_params.get("delivery_to") or "").strip()
    offset_minutes = parse_timezone_offset_to_minutes(
        crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    ) or 0

    def _utc_boundary(value: str, *, end: bool = False):
        try:
            boundary = datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError):
            return None
        if end:
            boundary += timedelta(days=1)
        return boundary - timedelta(minutes=offset_minutes)

    delivery_created_from = _utc_boundary(delivery_from)
    delivery_created_to = _utc_boundary(delivery_to, end=True)
    if delivery_created_from is None:
        delivery_from = ""
    if delivery_created_to is None:
        delivery_to = ""
    delivery_filter_kwargs = {
        "q": delivery_q,
        "status": delivery_status,
        "channel_id": delivery_channel_id,
        "event_type": delivery_event,
        "created_from": delivery_created_from,
        "created_to": delivery_created_to,
    }
    delivery_pagination_params = pagination_service.normalize_pagination_params(
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
        default_limit=10,
        max_limit=100,
    )
    delivery_total = notification_routing_service.count_deliveries(session, **delivery_filter_kwargs)
    delivery_rows = notification_routing_service.list_deliveries(
        session,
        limit=delivery_pagination_params.limit,
        offset=delivery_pagination_params.offset,
        locale=locale,
        **delivery_filter_kwargs,
    )
    delivery_pagination = pagination_service.build_pagination_data(
        page=delivery_pagination_params.page,
        limit=delivery_pagination_params.limit,
        total=delivery_total,
    )
    delivery_pagination_base = pagination_service.build_pagination_base(
        path="/notifications",
        params={
            "delivery_q": delivery_q,
            "delivery_status": delivery_status,
            "delivery_event": delivery_event,
            "delivery_channel": delivery_channel_id or "",
            "delivery_from": delivery_from,
            "delivery_to": delivery_to,
        },
        page_param="page",
        limit=delivery_pagination_params.limit,
        default_limit=10,
        limit_explicit=delivery_pagination_params.limit_explicit,
        limit_param="limit",
    )

    response = templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={
            **_layout_context(request=request, active="notifications"),
            "csrf_token": csrf_token,
            "notification_channels": channel_rows,
            "notification_templates": template_rows,
            "notification_policies": policy_rows,
            "notification_deliveries": delivery_rows,
            "delivery_pagination": delivery_pagination.as_dict(),
            "delivery_pagination_base": delivery_pagination_base,
            "delivery_filters": {
                "q": delivery_q,
                "status": delivery_status,
                "event": delivery_event,
                "channel_id": delivery_channel_id,
                "from": delivery_from,
                "to": delivery_to,
                "active": any((delivery_q, delivery_status, delivery_event, delivery_channel_id, delivery_from, delivery_to)),
            },
            "notification_delivery_statuses": ("pending", "sending", "sent", "retrying", "failed"),
            "notification_event_types": notification_routing_service.EVENT_TYPES,
            "notification_channel_types": notification_routing_service.CHANNEL_TYPES,
            "notification_template_channel_types": notification_routing_service.TEMPLATE_CHANNEL_TYPES,
            "notification_content_types": notification_routing_service.CONTENT_TYPES,
            "notification_failure_types": notification_routing_service.FAILURE_TYPES,
            "notification_template_variable_groups": notification_routing_service.template_variable_catalog(locale=locale),
            "notification_template_sample_contexts": {
                event_type: notification_routing_service.sample_template_context(locale=locale, event_type=event_type)
                for event_type in ("*", *notification_routing_service.EVENT_TYPES)
            },
            "notification_groups": groups,
            "notification_platforms": platforms,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


def _notification_redirect_error(request: Request, exc: ServiceError) -> RedirectResponse:
    if "CHANNEL" in exc.code:
        key = "notification.error.channel"
    elif "TEMPLATE" in exc.code:
        key = "notification.error.template"
    elif "POLICY" in exc.code:
        key = "notification.error.policy"
    else:
        key = "notification.error.operation"
    return RedirectResponse(url=f"/notifications?err={quote(translate(request.state.locale, key))}", status_code=303)


@router.post("/notifications/channels", summary="保存通知通道", description="创建或更新通知投递通道")
async def save_notification_channel(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    channel_id: int = Form(0),
    name: str = Form(""),
    channel_type: str = Form("webhook"),
    enabled: str = Form("0"),
    smtp_host: str = Form(""),
    smtp_port: str = Form("25"),
    smtp_user: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
    smtp_password: str = Form(""),
    webhook_url: str = Form(""),
    signing_secret: str = Form(""),
    authorization: str = Form(""),
    timeout: int = Form(10),
    allow_private: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        normalized_smtp_port = max(1, min(int(smtp_port or 25), 65535))
    except (TypeError, ValueError):
        normalized_smtp_port = 25
    config = {
        "host": smtp_host.strip(),
        "port": str(normalized_smtp_port),
        "user": smtp_user.strip(),
        "from": smtp_from.strip(),
        "to": smtp_to.strip(),
        "starttls": True,
        "timeout": max(1, min(int(timeout or 10), 30)),
        "allow_private": allow_private in {"1", "on"},
    }
    secrets = {
        "password": smtp_password,
        "url": webhook_url,
        "signing_secret": signing_secret,
        "authorization": authorization,
    }
    try:
        channel = notification_routing_service.save_channel(
            session,
            channel_id=channel_id or None,
            name=name,
            channel_type=channel_type,
            enabled=enabled in {"1", "on"},
            config=config,
            secrets=secrets,
        )
    except ServiceError as exc:
        return _notification_redirect_error(request, exc)
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_channel", channel.id, f"Saved channel: {channel.name} ({channel.channel_type})")
    return RedirectResponse(url="/notifications?msg=message.saved", status_code=303)


@router.post("/notifications/channels/test", summary="发送通知通道测试消息")
async def test_notification_channel(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    channel_id: int = Form(0),
    channel_type: str = Form("smtp"),
    smtp_host: str = Form(""),
    smtp_port: str = Form("25"),
    smtp_user: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
    smtp_password: str = Form(""),
    webhook_url: str = Form(""),
    signing_secret: str = Form(""),
    authorization: str = Form(""),
    timeout: str = Form("10"),
    allow_private: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        normalized_port = max(1, min(int(smtp_port or 25), 65535))
        normalized_timeout = max(1, min(int(timeout or 10), 30))
        success = notification_routing_service.test_channel(
            session,
            channel_id=channel_id or None,
            channel_type=channel_type,
            config={
                "host": smtp_host,
                "port": str(normalized_port),
                "user": smtp_user,
                "from": smtp_from,
                "to": smtp_to,
                "timeout": normalized_timeout,
                "allow_private": allow_private in {"1", "on"},
            },
            secrets={
                "password": smtp_password,
                "url": webhook_url,
                "signing_secret": signing_secret,
                "authorization": authorization,
            },
            subject=translate(
                request.state.locale,
                "notification.test_email.subject" if channel_type == "smtp" else "notification.test_channel.subject",
            ),
            content=translate(
                request.state.locale,
                "notification.test_email.content" if channel_type == "smtp" else "notification.test_channel.content",
            ),
        )
    except (ServiceError, TypeError, ValueError):
        return {
            "success": False,
            "error": {"code": "CHANNEL_TEST_FAILED", "message": translate(request.state.locale, "notification.test_channel.failed")},
        }
    except Exception:
        logger.warning("Notification channel test failed channel_type=%s channel_id=%s", channel_type, channel_id, exc_info=True)
        return {
            "success": False,
            "error": {"code": "CHANNEL_TEST_FAILED", "message": translate(request.state.locale, "notification.test_channel.failed")},
        }
    return {
        "success": bool(success),
        "message": translate(request.state.locale, "notification.test_channel.sent" if success else "notification.test_channel.failed"),
    }


@router.post("/notifications/channels/{channel_id}/enabled", summary="启用或停用通知通道")
async def set_notification_channel_enabled(
    request: Request,
    channel_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    enabled: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        item = notification_routing_service.set_channel_enabled(session, channel_id, enabled in {"1", "on"})
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.channel")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_channel", item.id, f"Set channel enabled={item.enabled}")
    return {"success": True, "enabled": item.enabled}


@router.post("/notifications/channels/{channel_id}/delete", summary="删除通知通道")
async def delete_notification_channel(
    request: Request,
    channel_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
):
    # This endpoint has no Form dependency, so FastAPI does not populate
    # request._form before entering the handler. Parse the multipart body first
    # so fastapi-csrf-protect reads the submitted token instead of treating the
    # raw multipart boundary as JSON.
    await request.form()
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        notification_routing_service.delete_channel(session, channel_id)
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.channel")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_channel", channel_id, "Deleted notification channel")
    return {"success": True, "deleted": channel_id}


@router.post("/notifications/templates", summary="保存通知模板")
async def save_notification_template(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    template_id: int = Form(0),
    name: str = Form(""),
    enabled: str = Form("0"),
    event_type: str = Form("*"),
    channel_type: str = Form("*"),
    locale: str = Form("zh-CN"),
    subject_template: str = Form(""),
    body_template: str = Form(""),
    content_type: str = Form("html"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        item = notification_routing_service.save_template(
            session,
            template_id=template_id or None,
            name=name,
            enabled=enabled in {"1", "on"},
            event_type=event_type,
            channel_type=channel_type,
            locale=locale,
            subject_template=subject_template,
            body_template=body_template,
            content_type=content_type,
        )
    except ServiceError as exc:
        return _notification_redirect_error(request, exc)
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_template", item.id, f"Saved template: {item.name}")
    return RedirectResponse(url="/notifications?msg=message.saved", status_code=303)


@router.post("/notifications/templates/{template_id}/enabled", summary="启用或停用通知模板")
async def set_notification_template_enabled(
    request: Request,
    template_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    enabled: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        item = notification_routing_service.set_template_enabled(session, template_id, enabled in {"1", "on"})
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.template")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_template", item.id, f"Set template enabled={item.enabled}")
    return {"success": True, "enabled": item.enabled}


@router.post("/notifications/templates/{template_id}/delete", summary="删除通知模板")
async def delete_notification_template(
    request: Request,
    template_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
):
    await request.form()
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        notification_routing_service.delete_template(session, template_id)
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.template")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_template", template_id, "Deleted notification template")
    return {"success": True, "deleted": template_id}


@router.post("/notifications/template-preview", summary="预览通知模板")
async def preview_notification_template(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    subject_template: str = Form(""),
    body_template: str = Form(""),
    content_type: str = Form("html"),
    locale: str = Form("zh-CN"),
    event_type: str = Form("*"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        return {
            "success": True,
            **notification_routing_service.preview_template(
                subject_template=subject_template,
                body_template=body_template,
                content_type=content_type,
                locale=locale,
                event_type=event_type,
            ),
        }
    except ServiceError as exc:
        return {
            "success": False,
            "error": {
                "code": exc.code,
                "message": translate(request.state.locale, "notification.error.template"),
                "detail": str(exc.context.get("detail") or ""),
            },
        }


@router.post("/notifications/policies", summary="保存通知策略")
async def save_notification_policy(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    policy_id: int = Form(0),
    name: str = Form(""),
    enabled: str = Form("0"),
    priority: int = Form(100),
    event_types: list[str] = Form([]),
    group_ids: list[int] = Form([]),
    include_descendants: str = Form("0"),
    platforms: list[str] = Form([]),
    failure_types: list[str] = Form([]),
    channel_ids: list[int] = Form([]),
    template_id: int = Form(...),
    stop_processing: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        item = notification_routing_service.save_policy(
            session,
            policy_id=policy_id or None,
            name=name,
            enabled=enabled in {"1", "on"},
            priority=priority,
            event_types=event_types,
            group_ids=group_ids,
            include_descendants=include_descendants in {"1", "on"},
            platforms=platforms,
            failure_types=failure_types,
            channel_ids=channel_ids,
            template_id=template_id or None,
            stop_processing=stop_processing in {"1", "on"},
        )
    except ServiceError as exc:
        return _notification_redirect_error(request, exc)
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_policy", item.id, f"Saved policy: {item.name}")
    return RedirectResponse(url="/notifications?msg=message.saved", status_code=303)


@router.post("/notifications/policies/{policy_id}/enabled", summary="启用或停用通知策略")
async def set_notification_policy_enabled(
    request: Request,
    policy_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    enabled: str = Form("0"),
):
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        item = notification_routing_service.set_policy_enabled(session, policy_id, enabled in {"1", "on"})
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.policy")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_policy", item.id, f"Set policy enabled={item.enabled}")
    return {"success": True, "enabled": item.enabled}


@router.post("/notifications/policies/{policy_id}/delete", summary="删除通知策略")
async def delete_notification_policy(
    request: Request,
    policy_id: int,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
):
    await request.form()
    await csrf_protect.validate_csrf(request)
    _require_permission(request, "notifications.update")
    try:
        notification_routing_service.delete_policy(session, policy_id)
    except ServiceError as exc:
        return {"success": False, "error": {"code": exc.code, "message": translate(request.state.locale, "notification.error.policy")}}
    _log_action(request, session, "UPDATE_NOTIFICATIONS", "notification_policy", policy_id, "Deleted notification policy")
    return {"success": True, "deleted": policy_id}


@router.post("/notifications/policies/simulate", summary="试算通知策略路由")
async def simulate_notification_policy_routes(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    session: Session = Depends(get_session),
    event_type: str = Form(""),
    group_id: int = Form(0),
    platform: str = Form(""),
    failure_type: str = Form(""),
):
    await csrf_protect.validate_csrf(request)
    _require_any_permission(request, ["notifications.view", "notifications.update"])
    try:
        result = notification_routing_service.simulate_policy_routes(
            session,
            event_type=event_type,
            group_id=group_id,
            platform=platform,
            failure_type=failure_type,
            locale=request.state.locale,
        )
    except ServiceError as exc:
        return {
            "success": False,
            "error": {
                "code": exc.code,
                "message": translate(request.state.locale, "notification.simulator.error"),
            },
        }
    return {"success": True, **result}


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
