from __future__ import annotations

from html import escape
import logging
from datetime import datetime
from uuid import UUID
from sqlmodel import Session

from app import crud
from app.core.settings import settings
from app.core.time import apply_timezone_offset, parse_timezone_offset_to_minutes
from app.models import BackupRecord, Device
from app.services.notification_service import send_email
from app.services import notification_routing_service, task_state_service
from app.services.backup_error_service import localize_backup_error_message
from app.i18n import get_current_locale, translate
from app.i18n.email import render_email_template
from app.i18n.validators import normalize_locale

logger = logging.getLogger(__name__)


def _alert_result(
    *,
    mode: str,
    rule_enabled: bool,
    matched: bool = False,
    email_attempted: bool = False,
    email_sent: bool = False,
    skipped: bool = False,
    reason: str = "",
    error: str = "",
) -> dict:
    result = {
        "mode": mode,
        "rule_enabled": bool(rule_enabled),
        "matched": bool(matched),
        "email_attempted": bool(email_attempted),
        "email_sent": bool(email_sent),
        "skipped": bool(skipped),
        "reason": reason,
    }
    if error:
        result["error"] = error
    return result


def _send_alert_email(
    session: Session | None,
    subject: str,
    content: str,
    *,
    mode: str,
    reason: str,
    locale: str | None = None,
    event_type: str = "backup_summary",
    source_key: str = "",
    payload: dict | None = None,
) -> dict:
    try:
        if session is None:
            email_sent = send_email(subject, content, content_type="html")
            attempted = True
        else:
            dispatch_result = notification_routing_service.dispatch_event(
                session,
                event_type=event_type,
                source_key=source_key,
                locale=locale or get_current_locale(),
                payload=payload or {},
                fallback_subject=subject,
                fallback_body=content,
                email_sender=send_email,
            )
            attempted = bool(dispatch_result["attempted"])
            email_sent = bool(dispatch_result["sent"])
        return _alert_result(
            mode=mode,
            rule_enabled=True,
            matched=True,
            email_attempted=attempted,
            email_sent=bool(email_sent),
            reason=reason if email_sent else ("no_channel_matched" if not attempted else "notification_send_failed"),
        )
    except Exception as exc:
        return _alert_result(
            mode=mode,
            rule_enabled=True,
            matched=True,
            email_attempted=True,
            email_sent=False,
            reason="email_send_failed",
            error=str(exc),
        )


def _format_datetime(dt: datetime | None, session: Session) -> str:
    if dt is None:
        return ""
    tz_str = crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    offset_minutes = parse_timezone_offset_to_minutes(tz_str) or 0
    local_value = apply_timezone_offset(dt, offset_minutes)
    if local_value is None:
        return ""
    return local_value.strftime("%Y-%m-%d %H:%M:%S")


def _config_changed_after_diff_rules(
    session: Session,
    *,
    device: Device,
    current_record: BackupRecord,
    previous_record: BackupRecord | None,
) -> bool:
    return bool(
        _config_change_summary_after_diff_rules(
            session,
            device=device,
            current_record=current_record,
            previous_record=previous_record,
        )
    )


def _config_change_summary_after_diff_rules(
    session: Session,
    *,
    device: Device,
    current_record: BackupRecord,
    previous_record: BackupRecord | None,
) -> dict | None:
    if not previous_record or not previous_record.config_text:
        return None
    from app.services import backup_service

    summary = backup_service.summarize_meaningful_config_change(
        session,
        current_text=current_record.config_text,
        previous_text=previous_record.config_text,
        current_device=device,
        previous_device=device,
    )
    return summary if summary.get("changed") else None


def _render_config_change_summary_html(summary: dict) -> str:
    sample_lines = list(summary.get("sample_lines") or [])
    sample_limit = int(summary.get("sample_limit") or len(sample_lines) or 0)
    total_sample_rows = int(summary.get("total_sample_rows") or len(sample_lines) or 0)
    context_lines = int(summary.get("context_lines") or 0)

    sample_html = ""
    if sample_lines:
        items: list[str] = []
        for item in sample_lines:
            prefix = str(item.get("prefix") or "")
            text = str(item.get("text") or "")
            if not text.strip():
                text = "(空行)"
            kind = str(item.get("kind") or "")
            if kind == "add":
                color = "#198754"
            elif kind == "del":
                color = "#dc3545"
            elif kind == "context":
                color = "#6c757d"
            else:
                color = "#6c757d"
            items.append(
                f'<li style="margin: 0 0 6px 0;"><code style="color: {color};">{escape(prefix)} {escape(text)}</code></li>'
            )
        sample_html = f"""
        <div style="margin-top: 12px;">
            <div style="font-weight: bold; margin-bottom: 6px;">变更片段（含前后各 {context_lines} 行）</div>
            <ul style="margin: 0; padding-left: 20px;">
                {''.join(items)}
            </ul>
        </div>
        """

    more_hint = ""
    if total_sample_rows > sample_limit:
        more_hint = f"当前仅展示前 {len(sample_lines)} 行片段。"
    footer = more_hint or ""

    return f"""
    <div style="margin-top: 16px; padding: 12px 14px; background-color: #fff8e1; border-left: 4px solid #f0ad4e; max-width: 800px;">
        <div style="font-weight: bold; margin-bottom: 8px;">已应用 Diff 忽略规则</div>
        {sample_html}
        <div style="margin-top: 12px; color: #6c757d; font-size: 12px;">{footer}</div>
    </div>
    """


def _render_config_change_summary_compact_html(summary: dict) -> str:
    sample_lines = list(summary.get("sample_lines") or [])
    sample_limit = int(summary.get("sample_limit") or len(sample_lines) or 0)
    total_sample_rows = int(summary.get("total_sample_rows") or len(sample_lines) or 0)
    context_lines = int(summary.get("context_lines") or 0)
    parts: list[str] = []
    if sample_lines:
        items: list[str] = []
        for item in sample_lines:
            prefix = str(item.get("prefix") or "")
            text = str(item.get("text") or "")
            if not text.strip():
                text = "(空行)"
            kind = str(item.get("kind") or "")
            if kind == "add":
                color = "#198754"
            elif kind == "del":
                color = "#dc3545"
            elif kind == "context":
                color = "#6c757d"
            else:
                color = "#6c757d"
            items.append(
                f'<li style="margin: 0 0 4px 0;"><code style="color: {color};">{escape(prefix)} {escape(text)}</code></li>'
            )
        parts.append(
            f"""
            <div style="margin-bottom: 6px;">
                <div style="font-weight: bold; margin-bottom: 4px;">变更片段（含前后各 {context_lines} 行）</div>
                <ul style="margin: 0; padding-left: 18px;">
                    {''.join(items)}
                </ul>
            </div>
            """
        )
    more_hint = ""
    if total_sample_rows > sample_limit:
        more_hint = f"当前仅展示前 {len(sample_lines)} 行片段。"
    parts.append(f'<div style="color: #6c757d; font-size: 12px;">{more_hint}</div>')
    return "".join(parts)


def _render_localized_change_summary_html(summary: dict, locale: str) -> str:
    normalized = normalize_locale(locale)
    sample_lines = list(summary.get("sample_lines") or [])
    context_lines = int(summary.get("context_lines") or 0)
    sample_limit = int(summary.get("sample_limit") or len(sample_lines) or 0)
    total_sample_rows = int(summary.get("total_sample_rows") or len(sample_lines) or 0)
    items: list[str] = []
    for item in sample_lines:
        prefix = escape(str(item.get("prefix") or ""))
        text = escape(str(item.get("text") or "") or translate(normalized, "email.blank_line"))
        color = {
            "add": "#198754",
            "del": "#dc3545",
            "context": "#6c757d",
        }.get(str(item.get("kind") or ""), "#6c757d")
        items.append(
            f'<li style="margin:0 0 4px 0"><code style="color:{color}">{prefix} {text}</code></li>'
        )
    if not items:
        return ""
    truncated = ""
    if total_sample_rows > sample_limit:
        truncated = escape(
            translate(normalized, "email.changed_lines_truncated", {"count": len(sample_lines)})
        )
    return (
        '<div style="margin-top:12px">'
        f'<div style="font-weight:bold;margin-bottom:6px">{escape(translate(normalized, "email.diff_rules_applied"))}</div>'
        f'<div style="font-weight:bold;margin-bottom:4px">{escape(translate(normalized, "email.changed_lines_context", {"context": context_lines}))}</div>'
        '<ul style="margin:0;padding-left:18px">'
        + "".join(items)
        + "</ul>"
        + (f'<div style="color:#6c757d;font-size:12px">{truncated}</div>' if truncated else "")
        + "</div>"
    )


def _structured_change_lines(summary: dict | None) -> list[dict[str, str]]:
    return [
        {
            "prefix": str(item.get("prefix") or ""),
            "text": str(item.get("text") or ""),
            "kind": str(item.get("kind") or "context"),
        }
        for item in ((summary or {}).get("sample_lines") or [])
        if isinstance(item, dict)
    ]


def _structured_change_metadata(summary: dict | None) -> dict[str, int | list[dict[str, str]]]:
    value = summary or {}
    lines = _structured_change_lines(value)
    return {
        "change_lines": lines,
        "change_context_lines": int(value.get("context_lines") or 0),
        "change_total_rows": int(value.get("total_sample_rows") or len(lines)),
        "change_sample_limit": int(value.get("sample_limit") or len(lines)),
    }


def check_and_alert(session: Session, record: BackupRecord, skip_email: bool = False) -> dict:
    """
    检查备份记录并根据规则触发告警
    :param skip_email: 是否跳过发送邮件（用于批量任务，后续统一发送汇总邮件）
    """
    device = crud.get_device(session, record.device_id)
    if not device:
        return _alert_result(mode="device_missing", rule_enabled=False, skipped=True, reason="device_missing")

    always_send = notification_routing_service.has_unconditional_summary_policy(session)
    if always_send and not skip_email:
        return _send_single_backup_summary_email(session, device, record)

    # 1. 备份失败告警
    if not record.success:
        return _handle_failure_alert(session, device, record, skip_email)
    else:
        # 2. 配置变更告警
        return _handle_config_change_alert(session, device, record, skip_email)


def _send_single_backup_summary_email(session: Session, device: Device, record: BackupRecord) -> dict:
    prev_success = None
    change_summary = None
    if record.success:
        backups = crud.list_device_backups(session, device.id, limit=2)
        for backup in backups:
            if backup.id != record.id and backup.success:
                prev_success = backup
                break
        change_summary = _config_change_summary_after_diff_rules(
            session,
            device=device,
            current_record=record,
            previous_record=prev_success,
        )

    status_label = "成功"
    status_color = "#5cb85c"
    status_detail = "本次单设备备份执行成功。"
    if str(record.status or "").strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED:
        status_label = "终止"
        status_color = "#f0ad4e"
        status_detail = record.error_message or "任务已终止"
    elif not record.success:
        status_label = "失败"
        status_color = "#d9534f"
        status_detail = record.error_message or "未知错误"
    elif change_summary:
        status_detail = "本次备份成功，并检测到配置发生变更。"

    duration_str = f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-"
    subject = f"【备份汇总报告】{device.name}({device.host}) {status_label}"
    content = f"""
    <html>
    <body>
        <h2>备份汇总报告</h2>
        <p><strong>任务时间:</strong> {_format_datetime(record.started_at, session)}</p>
        <p><strong>统计结果:</strong> 总计 1 台，成功 <span style="color: {'#5cb85c' if record.success else '#6c757d'};">{1 if record.success else 0}</span> 台，失败 <span style="color: {'#d9534f' if not record.success and str(record.status or '').strip() != task_state_service.BACKUP_RECORD_STATUS_CANCELLED else '#6c757d'};">{1 if (not record.success and str(record.status or '').strip() != task_state_service.BACKUP_RECORD_STATUS_CANCELLED) else 0}</span> 台，终止 <span style="color: {'#f0ad4e' if str(record.status or '').strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED else '#6c757d'};">{1 if str(record.status or '').strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED else 0}</span> 台</p>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 800px;">
            <tr><th style="background-color: #f2f2f2; width: 120px; text-align: left;">设备名称</th><td>{escape(device.name)}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">设备地址</th><td>{escape(device.host)}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">执行结果</th><td style="color: {status_color};">{status_label}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">耗时</th><td>{duration_str}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">详情</th><td>{escape(status_detail)}</td></tr>
        </table>
    """
    if not record.success and record.error_message:
        content += f"""
        <h3 style="color: #d9534f;">失败详情</h3>
        <div style="max-width: 800px; padding: 10px 12px; border: 1px solid #f5c6cb; background-color: #f8d7da; color: #721c24;">
            {escape(record.error_message)}
        </div>
        """
    if change_summary:
        content += f"""
        <h3 style="color: #f0ad4e;">配置变更摘要</h3>
        {_render_config_change_summary_compact_html(change_summary)}
        """
    content += """
    </body>
    </html>
    """
    locale = normalize_locale(getattr(record, "locale", None) or get_current_locale())
    subject = translate(
        locale,
        "email.summary.subject",
        {"device": device.name, "host": device.host, "status": task_state_service.get_backup_record_status_label(record.status, locale)},
    )
    if change_summary:
        detail_key = "email.summary.detail.changed"
    elif str(record.status or "").strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED:
        detail_key = "email.summary.detail.cancelled"
    elif record.success:
        detail_key = "email.summary.detail.succeeded"
    else:
        detail_key = "email.summary.detail.failed"
    content = render_email_template(
        "backup_single_summary.html",
        locale=locale,
        context={
            "device": device,
            "record": record,
            "task_time": _format_datetime(record.started_at, session),
            "status_label": task_state_service.get_backup_record_status_label(record.status, locale),
            "status_detail": translate(locale, detail_key, fallback=status_detail),
            "duration": duration_str,
            "summary_html": _render_localized_change_summary_html(change_summary, locale) if change_summary else "",
        },
    )
    event_type = (
        "task_cancelled"
        if str(record.status or "").strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED
        else "backup_summary"
    )
    return _send_alert_email(
        session,
        subject,
        content,
        mode="single_summary",
        reason="always_send_summary",
        locale=locale,
        event_type=event_type,
        source_key=f"{event_type}:record:{record.id}",
        payload={
            "event_type": event_type,
            "device_id": device.id,
            "device_name": device.name,
            "device_host": device.host,
            "group_id": device.group_id,
            "platform": device.platform,
            "failure_type": record.failure_type or "",
            "error_message": status_detail,
            "localized_error_message": status_detail,
            "duration": duration_str,
            "task_time": _format_datetime(record.started_at, session),
            "success": bool(record.success),
            "cancelled": event_type == "task_cancelled",
            "changed": bool(change_summary),
            **_structured_change_metadata(change_summary),
        },
    )

def _handle_failure_alert(session: Session, device: Device, record: BackupRecord, skip_email: bool = False) -> dict:
    """
    处理备份失败告警
    """
    alert_on_fail = notification_routing_service.has_enabled_policy_for_event(session, "backup_failed")
    if skip_email:
        return _alert_result(mode="failure", rule_enabled=alert_on_fail, skipped=True, reason="skip_email")
    if not alert_on_fail:
        return _alert_result(mode="failure", rule_enabled=False, skipped=True, reason="rule_disabled")

    subject = f"【告警】设备备份失败: {device.name}({device.host})"
    
    # 构建 HTML 内容
    duration_str = f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-"
    content = f"""
    <html>
    <body>
        <h3 style="color: #d9534f;">设备备份失败</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 800px;">
            <tr><th style="background-color: #f2f2f2; width: 120px; text-align: left;">设备名称</th><td>{device.name}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">设备地址</th><td>{device.host}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">备份时间</th><td>{_format_datetime(record.started_at, session)}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">错误类型</th><td>{record.failure_type or 'UNKNOWN'}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">耗时</th><td>{duration_str}</td></tr>
            <tr><th style="background-color: #f2f2f2; text-align: left;">错误详情</th><td style="color: #d9534f;">{record.error_message or '未知错误'}</td></tr>
        </table>
    </body>
    </html>
    """
    
    locale = normalize_locale(getattr(record, "locale", None) or get_current_locale())
    subject = translate(locale, "email.backup_failure.subject", {"device": device.name, "host": device.host})
    content = render_email_template(
        "backup_failure.html",
        locale=locale,
        context={
            "device": device,
            "record": record,
            "backup_time": _format_datetime(record.started_at, session),
            "duration": duration_str,
            "error_message": localize_backup_error_message(
                record.error_message,
                record.failure_type,
                locale=locale,
            ) or translate(locale, "email.unknown_error"),
        },
    )
    return _send_alert_email(
        session,
        subject,
        content,
        mode="failure",
        reason="failure_rule_matched",
        locale=locale,
        event_type="backup_failed",
        source_key=f"backup_failed:record:{record.id}",
        payload={
            "event_type": "backup_failed",
            "device_id": device.id,
            "device_name": device.name,
            "device_host": device.host,
            "group_id": device.group_id,
            "platform": device.platform,
            "failure_type": record.failure_type or "UNKNOWN",
            "error_message": record.error_message or "",
            "localized_error_message": localize_backup_error_message(
                record.error_message,
                record.failure_type,
                locale=locale,
            ) or translate(locale, "email.unknown_error"),
            "duration": duration_str,
            "task_time": _format_datetime(record.started_at, session),
            "success": False,
            "cancelled": False,
            "changed": False,
        },
    )

def _handle_config_change_alert(session: Session, device: Device, record: BackupRecord, skip_email: bool = False) -> dict:
    """
    处理配置变更告警
    """
    alert_on_change = notification_routing_service.has_enabled_policy_for_event(session, "config_changed")
    if skip_email:
        return _alert_result(mode="config_change", rule_enabled=alert_on_change, skipped=True, reason="skip_email")
    if not alert_on_change:
        return _alert_result(mode="config_change", rule_enabled=False, skipped=True, reason="rule_disabled")

    # 获取上一个成功的备份记录
    backups = crud.list_device_backups(session, device.id, limit=2)
    # 过滤出成功的记录，排除当前这一条
    prev_success = None
    for b in backups:
        if b.id != record.id and b.success:
            prev_success = b
            break
    
    summary = _config_change_summary_after_diff_rules(
        session,
        device=device,
        current_record=record,
        previous_record=prev_success,
    )
    if summary:
        subject = f"【提醒】设备配置已变更: {device.name}({device.host})"
        summary_html = _render_config_change_summary_html(summary)
        
        # 构建 HTML 内容
        content = f"""
        <html>
        <body>
            <h3 style="color: #f0ad4e;">设备配置已变更</h3>
            <p>检测到配置与上一次成功备份相比发生了变更，请确认是否为预期操作。</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 800px;">
                <tr><th style="background-color: #f2f2f2; width: 120px; text-align: left;">设备名称</th><td>{device.name}</td></tr>
                <tr><th style="background-color: #f2f2f2; text-align: left;">设备地址</th><td>{device.host}</td></tr>
            </table>
            {summary_html}
        </body>
        </html>
        """
        locale = normalize_locale(getattr(record, "locale", None) or get_current_locale())
        subject = translate(locale, "email.config_changed.subject", {"device": device.name, "host": device.host})
        content = render_email_template(
            "backup_config_changed.html",
            locale=locale,
            context={"device": device, "record": record, "summary_html": _render_localized_change_summary_html(summary, locale)},
        )
        return _send_alert_email(
            session,
            subject,
            content,
            mode="config_change",
            reason="config_changed",
            locale=locale,
            event_type="config_changed",
            source_key=f"config_changed:record:{record.id}",
            payload={
                "event_type": "config_changed",
                "device_id": device.id,
                "device_name": device.name,
                "device_host": device.host,
                "group_id": device.group_id,
                "platform": device.platform,
                "failure_type": "",
                "error_message": "",
                "duration": f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-",
                "task_time": _format_datetime(record.started_at, session),
                "success": True,
                "cancelled": False,
                "changed": True,
                **_structured_change_metadata(summary),
            },
        )

    return _alert_result(
        mode="config_change",
        rule_enabled=True,
        matched=False,
        email_attempted=False,
        email_sent=False,
        skipped=False,
        reason="no_config_change",
    )

def check_and_alert_batch(
    session: Session,
    run_id: UUID,
    *,
    records: list[BackupRecord] | None = None,
):
    """
    检查批量备份任务并发送汇总告警邮件
    """
    run = crud.get_schedule_run(session, run_id)
    if not run:
        return {"mode": "batch_summary", "skipped": True, "reason": "run_missing"}

    # Finalizer 应传入其完成统计所使用的同一记录快照，避免批次汇总数字
    # 与邮件明细因二次查询时序不同而不一致。保留查询路径供独立调用兼容。
    if records is None:
        items = crud.list_schedule_run_items(session, run_id)
        if not items:
            return {"mode": "batch_summary", "skipped": True, "reason": "no_items"}
        backup_ids = [item.backup_id for item in items]
        records = crud.list_backups_by_ids(session, backup_ids)
    else:
        records = list(records)
    if not records:
        return {"mode": "batch_summary", "skipped": True, "reason": "no_records"}
    
    failed_records = []
    cancelled_records = []
    changed_records = []
    
    # 获取配置
    alert_on_fail = notification_routing_service.is_builtin_policy_enabled(session, "failure")
    alert_on_change = notification_routing_service.is_builtin_policy_enabled(session, "config_change")
    always_send = (
        notification_routing_service.has_unconditional_summary_policy(session)
        if session is not None
        else notification_routing_service.is_builtin_policy_enabled(session, "summary")
    )

    for record in records:
        device = crud.get_device(session, record.device_id)
        if not device:
            continue
            
        if str(record.status or "").strip() == task_state_service.BACKUP_RECORD_STATUS_CANCELLED:
            cancelled_records.append((device, record))
        elif not record.success:
            failed_records.append((device, record))
        else:
            # 始终检查配置变更，以便汇总报告展示（如果需要）
            # 获取上一个成功的备份记录
            prev_backups = crud.list_device_backups(session, device.id, limit=2)
            prev_success = None
            for b in prev_backups:
                if b.id != record.id and b.success:
                    prev_success = b
                    break
            summary = _config_change_summary_after_diff_rules(
                session,
                device=device,
                current_record=record,
                previous_record=prev_success,
            )
            if summary:
                changed_records.append((device, record, summary))

    # 决定是否发送邮件
    # 1. 如果开启了“始终发送汇总报告”，则发送
    # 2. 如果未开启汇总，但开启了“失败告警”且有失败，则发送
    # 3. 如果未开启汇总，但开启了“变更提醒”且有变更，则发送
    should_send = always_send
    trigger_reason = "always_send_summary" if always_send else ""
    if not should_send and alert_on_fail and (failed_records or cancelled_records):
        should_send = True
        trigger_reason = "failure_rule_matched"
    if not should_send and alert_on_change and changed_records:
        should_send = True
        trigger_reason = "config_changed"

    if not should_send:
        any_rule_enabled = bool(always_send or alert_on_fail or alert_on_change)
        return {
            "mode": "batch_summary",
            "rule_enabled": any_rule_enabled,
            "matched": False,
            "email_attempted": False,
            "email_sent": False,
            "skipped": True,
            "reason": "no_rule_matched" if any_rule_enabled else "rule_disabled",
            "trigger_reason": "no_rule_matched",
            "failed_count": len(failed_records),
            "cancelled_count": len(cancelled_records),
            "changed_count": len(changed_records),
            "always_send": bool(always_send),
            "alert_on_fail": bool(alert_on_fail),
            "alert_on_change": bool(alert_on_change),
        }
    if not should_send:
        trigger_reason = "custom_policy"

    # 构建汇总邮件内容
    subject = f"【备份汇总报告】"
    if failed_records:
        subject += f"发现 {len(failed_records)} 台设备备份失败"
    elif cancelled_records:
        subject += f"发现 {len(cancelled_records)} 台任务被终止"
    elif changed_records:
        subject += f"发现 {len(changed_records)} 台设备配置变更"
    else:
        subject += "全部备份成功"
        
    content = f"""
    <html>
    <body>
        <h2>备份汇总报告</h2>
        <p><strong>任务时间:</strong> {_format_datetime(run.started_at, session)}</p>
        <p><strong>统计结果:</strong> 总计 {run.total_devices} 台，成功 <span style="color: #5cb85c;">{run.success_count}</span> 台，失败 <span style="color: #d9534f;">{run.fail_count}</span> 台，终止 <span style="color: #f0ad4e;">{len(cancelled_records)}</span> 台</p>
    """

    if failed_records:
        content += """
        <h3 style="color: #d9534f;">失败列表</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1000px;">
            <tr style="background-color: #f2f2f2;">
                <th style="text-align: left; width: 20%;">设备名称</th>
                <th style="text-align: left; width: 20%;">设备地址</th>
                <th style="text-align: left; width: 15%;">耗时</th>
                <th style="text-align: left; width: 15%;">错误类型</th>
                <th style="text-align: left; width: 30%;">错误详情</th>
            </tr>
        """
        for device, record in failed_records:
            ftype = record.failure_type or "-"
            dur = f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-"
            err_msg = record.error_message or '未知错误'
            content += f"""
            <tr>
                <td>{device.name}</td>
                <td>{device.host}</td>
                <td>{dur}</td>
                <td>{ftype}</td>
                <td style="color: #d9534f;">{err_msg}</td>
            </tr>
            """
        content += "</table><br>"

    if cancelled_records:
        content += """
        <h3 style="color: #f0ad4e;">终止列表</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1000px;">
            <tr style="background-color: #f2f2f2;">
                <th style="text-align: left; width: 20%;">设备名称</th>
                <th style="text-align: left; width: 20%;">设备地址</th>
                <th style="text-align: left; width: 15%;">结束时间</th>
                <th style="text-align: left; width: 15%;">类型</th>
                <th style="text-align: left; width: 30%;">详情</th>
            </tr>
        """
        for device, record in cancelled_records:
            content += f"""
            <tr>
                <td>{device.name}</td>
                <td>{device.host}</td>
                <td>{_format_datetime(record.finished_at, session)}</td>
                <td>{record.failure_type or 'CANCELLED'}</td>
                <td style="color: #f0ad4e;">{record.error_message or '任务已终止'}</td>
            </tr>
            """
        content += "</table><br>"

    if changed_records:
        content += """
        <h3 style="color: #f0ad4e;">配置变更列表</h3>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1000px;">
            <tr style="background-color: #f2f2f2;">
                <th style="text-align: left; width: 20%;">设备名称</th>
                <th style="text-align: left; width: 20%;">设备地址</th>
                <th style="text-align: left; width: 60%;">变更摘要</th>
            </tr>
        """
        for device, record, summary in changed_records:
            summary_html = _render_config_change_summary_compact_html(summary)
            content += f"""
            <tr>
                <td>{escape(device.name)}</td>
                <td>{escape(device.host)}</td>
                <td>{summary_html}</td>
            </tr>
            """
        content += "</table><br>"

    content += """
    </body>
    </html>
    """

    locale = normalize_locale(
        getattr(records[0], "locale", None) if records else get_current_locale()
    )
    subject_key = "email.batch.subject.failed" if failed_records else (
        "email.batch.subject.cancelled" if cancelled_records else (
            "email.batch.subject.changed" if changed_records else "email.batch.subject.succeeded"
        )
    )
    subject = translate(
        locale,
        subject_key,
        {"failed": len(failed_records), "cancelled": len(cancelled_records), "changed": len(changed_records)},
    )
    content = render_email_template(
        "backup_batch_summary.html",
        locale=locale,
        context={
            "run": run,
            "task_time": _format_datetime(run.started_at, session),
            "cancelled": cancelled_records,
            "failed_records": [
                {
                    "device": device,
                    "record": record,
                    "duration": f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-",
                    "error_message": localize_backup_error_message(
                        record.error_message,
                        record.failure_type,
                        locale=locale,
                    ) or translate(locale, "email.unknown_error"),
                }
                for device, record in failed_records
            ],
            "cancelled_records": cancelled_records,
            "changed_records": [
                (device, record, _render_localized_change_summary_html(summary, locale))
                for device, record, summary in changed_records
            ],
        },
    )
    changed_ids = {record.id for _, record, _ in changed_records}
    changed_summary_html = {
        record.id: _render_localized_change_summary_html(summary, locale)
        for _, record, summary in changed_records
    }
    changed_summary_metadata = {
        record.id: _structured_change_metadata(summary)
        for _, record, summary in changed_records
    }
    cancelled_ids = {record.id for _, record in cancelled_records}
    batch_items = []
    for record in records:
        device = crud.get_device(session, record.device_id)
        if not device:
            continue
        batch_items.append(
            {
                "device_id": device.id,
                "device_name": device.name,
                "device_host": device.host,
                "group_id": device.group_id,
                "platform": device.platform,
                "failure_type": record.failure_type or ("CANCELLED" if record.id in cancelled_ids else ""),
                "error_message": record.error_message or "",
                "localized_error_message": localize_backup_error_message(
                    record.error_message,
                    record.failure_type,
                    locale=locale,
                ) or "",
                "duration": f"{record.duration_seconds:.2f}s" if record.duration_seconds is not None else "-",
                "finished_at": _format_datetime(record.finished_at, session) if record.finished_at else "-",
                "success": bool(record.success),
                "cancelled": record.id in cancelled_ids,
                "changed": record.id in changed_ids,
                "change_summary_html": changed_summary_html.get(record.id, ""),
                **changed_summary_metadata.get(record.id, _structured_change_metadata(None)),
            }
        )
    try:
        if session is None:
            email_sent = send_email(subject, content, content_type="html")
            attempted = True
        else:
            dispatch_result = notification_routing_service.dispatch_event(
                session,
                event_type="backup_summary",
                source_key=f"backup_summary:run:{run_id}",
                locale=locale,
                payload={
                    "event_type": "backup_summary",
                    "run_id": str(run_id),
                    "task_time": _format_datetime(run.started_at, session),
                    "total_count": len(batch_items),
                    "success_count": sum(1 for item in batch_items if item["success"]),
                    "failed_count": len(failed_records),
                    "cancelled_count": len(cancelled_records),
                    "changed_count": len(changed_records),
                    "items": batch_items,
                },
                fallback_subject=subject,
                fallback_body=content,
                email_sender=send_email,
            )
            attempted = bool(dispatch_result["attempted"])
            email_sent = bool(dispatch_result["sent"])
        reason = "batch_summary_sent" if email_sent else ("no_channel_matched" if not attempted else "notification_send_failed")
        error = ""
    except Exception as exc:
        attempted = True
        email_sent = False
        reason = "notification_send_failed"
        error = str(exc)
    return {
        "mode": "batch_summary",
        "rule_enabled": True,
        "matched": True,
        "email_attempted": attempted,
        "email_sent": bool(email_sent),
        "skipped": False,
        "reason": reason,
        "trigger_reason": trigger_reason,
        "error": error,
        "failed_count": len(failed_records),
        "cancelled_count": len(cancelled_records),
        "changed_count": len(changed_records),
        "always_send": bool(always_send),
        "alert_on_fail": bool(alert_on_fail),
        "alert_on_change": bool(alert_on_change),
    }
