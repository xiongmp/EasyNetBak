from __future__ import annotations

from sqlmodel import Session, select

from app import crud
from app.models import AuditLog
from app.routers.web_context import _dt_local_str
from app.services import export_service, pagination_service
from app.i18n import get_current_locale, locale_capabilities, translate


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
    "TERMINATE_SCHEDULE_RUN_PENDING": "终止未运行任务",
    "TERMINATE_SCHEDULE_RUN_SELECTED": "批量终止所选任务",
    "RETRY_SCHEDULE_RUN": "重试运行任务",
    "RETRY_SCHEDULE_RUN_SELECTED": "批量重试所选任务",
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
    "schedule_run": "运行记录",
    "settings": "系统设置",
    "storage_settings": "存储设置",
    "notifications": "通知设置",
    "notification_channel": "通知通道",
    "notification_template": "通知模板",
    "notification_policy": "通知策略",
    "user": "用户",
    "role": "角色",
}

LOGIN_STATUS_TEXT = {
    "success": "登录成功",
    "fail": "登录失败",
    "logout": "登出",
}


def translate_audit_action(action: str, locale: str | None = None) -> str:
    selected_locale = locale or get_current_locale()
    value = translate(selected_locale, f"audit.action.{action}", fallback=AUDIT_ACTION_MAP.get(action, action))
    if not locale_capabilities(selected_locale).uses_han and any("\u4e00" <= char <= "\u9fff" for char in value):
        return action.replace("_", " ").strip().title()
    return value


def translate_audit_resource(resource_type: str, locale: str | None = None) -> str:
    selected_locale = locale or get_current_locale()
    value = translate(selected_locale, f"audit.resource.{resource_type}", fallback=AUDIT_RESOURCE_MAP.get(resource_type, resource_type))
    if not locale_capabilities(selected_locale).uses_han and any("\u4e00" <= char <= "\u9fff" for char in value):
        return resource_type.replace("_", " ").strip().title()
    return value


def translate_login_status(status: str, locale: str | None = None) -> str:
    return translate(locale or get_current_locale(), f"login.status.{status}", fallback=LOGIN_STATUS_TEXT.get(status, status))


def translate_login_fail_reason(reason: str | None, locale: str | None = None) -> str:
    if not reason:
        return ""
    code = reason.strip().lower().replace(" ", "_")
    return translate(locale or get_current_locale(), f"login.fail_reason.{code}", fallback=reason)


def normalize_login_status(status: str | None) -> str | None:
    value = (status or "").strip().lower()
    return value if value in LOGIN_STATUS_TEXT else None


def get_audit_logs_page_payload(
    session: Session,
    *,
    q: str | None,
    action: str | None,
    resource_type: str | None,
    page: int,
    limit: int,
    limit_in_query: bool,
) -> dict[str, object]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=limit_in_query,
    )
    logs = crud.list_audit_logs(
        session,
        q=q,
        action=action,
        resource_type=resource_type,
        limit=params.limit,
        offset=params.offset,
    )
    total = crud.count_audit_logs(session, q=q, action=action, resource_type=resource_type)
    pagination = pagination_service.build_pagination_data(page=params.page, limit=params.limit, total=total)
    all_actions = session.exec(select(AuditLog.action).distinct()).all()
    all_resource_types = session.exec(select(AuditLog.resource_type).distinct()).all()
    pagination_base = pagination_service.build_pagination_base(
        path="/audit-logs",
        params={
            "q": q or "",
            "action": action or "",
            "resource_type": resource_type or "",
        },
        limit=params.limit,
        limit_explicit=params.limit_explicit,
    )
    return {
        "logs": logs,
        "q": q or "",
        "action": action or "",
        "resource_type": resource_type or "",
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
        "all_actions": all_actions,
        "all_resource_types": all_resource_types,
        "action_map": {value: translate_audit_action(value) for value in all_actions},
        "resource_map": {value: translate_audit_resource(value) for value in all_resource_types},
    }


def export_audit_logs_csv(
    session: Session,
    *,
    q: str | None,
    action: str | None,
    resource_type: str | None,
    offset_minutes: int,
):
    logs = crud.list_audit_logs(session, q=q, action=action, resource_type=resource_type, limit=10000, offset=0)
    rows = (
        (
            _dt_local_str(log.created_at, offset_minutes=offset_minutes),
            log.user_id or "",
            log.username or "",
            translate_audit_action(log.action),
            translate_audit_resource(log.resource_type),
            log.resource_id or "",
            log.details or "",
            log.ip_address or "",
        )
        for log in logs
    )
    return export_service.csv_streaming_response(
        filename=f"audit_logs_{get_current_locale()}.csv",
        headers=[
            translate(get_current_locale(), f"audit.csv.{key}", fallback=label)
            for key, label in (
                ("time", "时间"), ("user_id", "用户ID"), ("username", "用户名"),
                ("action", "操作"), ("resource_type", "资源类型"), ("resource_id", "资源ID"),
                ("details", "详情"), ("ip_address", "IP地址"),
            )
        ],
        rows=rows,
    )


def get_login_logs_page_payload(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    page: int,
    limit: int,
    limit_in_query: bool,
) -> dict[str, object]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=limit_in_query,
    )
    status_filter = normalize_login_status(status)
    logs = crud.list_login_logs(session, q=q, status=status_filter, limit=params.limit, offset=params.offset)
    total = crud.count_login_logs(session, q=q, status=status_filter)
    pagination = pagination_service.build_pagination_data(page=params.page, limit=params.limit, total=total)
    pagination_base = pagination_service.build_pagination_base(
        path="/login-logs",
        params={"q": q or "", "status": status or ""},
        limit=params.limit,
        limit_explicit=params.limit_explicit,
    )
    return {
        "logs": logs,
        "q": q or "",
        "status": status or "",
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
        "login_status_map": {value: translate_login_status(value) for value in LOGIN_STATUS_TEXT},
        "translate_login_fail_reason": translate_login_fail_reason,
    }


def export_login_logs_csv(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    offset_minutes: int,
):
    status_filter = normalize_login_status(status)
    logs = crud.list_login_logs(session, q=q, status=status_filter, limit=100000, offset=0)
    rows = (
        (
            log.id,
            _dt_local_str(log.created_at, offset_minutes=offset_minutes),
            log.username,
            translate_login_status(log.status or "unknown"),
            log.ip_address or "",
            log.user_agent or "",
            translate_login_fail_reason(log.fail_reason),
        )
        for log in logs
    )
    return export_service.csv_streaming_response(
        filename=f"login_logs_{get_current_locale()}.csv",
        headers=[
            translate(get_current_locale(), f"login.csv.{key}", fallback=label)
            for key, label in (
                ("id", "ID"), ("time", "时间"), ("username", "用户名"), ("status", "状态"),
                ("ip_address", "IP地址"), ("user_agent", "浏览器/客户端"), ("fail_reason", "失败原因"),
            )
        ],
        rows=rows,
    )
