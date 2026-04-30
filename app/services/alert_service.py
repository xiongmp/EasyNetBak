from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID
from sqlmodel import Session

from app import crud
from app.core.settings import settings
from app.core.time import apply_timezone_offset, parse_timezone_offset_to_minutes
from app.models import BackupRecord, Device
from app.services.notification_service import send_email
from app.services import task_state_service

logger = logging.getLogger(__name__)

def _format_datetime(dt: datetime | None, session: Session) -> str:
    if dt is None:
        return ""
    tz_str = crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    offset_minutes = parse_timezone_offset_to_minutes(tz_str) or 0
    local_value = apply_timezone_offset(dt, offset_minutes)
    if local_value is None:
        return ""
    return local_value.strftime("%Y-%m-%d %H:%M:%S")

def check_and_alert(session: Session, record: BackupRecord, skip_email: bool = False):
    """
    检查备份记录并根据规则触发告警
    :param skip_email: 是否跳过发送邮件（用于批量任务，后续统一发送汇总邮件）
    """
    device = crud.get_device(session, record.device_id)
    if not device:
        return

    # 1. 备份失败告警
    if not record.success:
        _handle_failure_alert(session, device, record, skip_email)
    else:
        # 2. 配置变更告警
        _handle_config_change_alert(session, device, record, skip_email)

def _handle_failure_alert(session: Session, device: Device, record: BackupRecord, skip_email: bool = False):
    """
    处理备份失败告警
    """
    alert_on_fail = crud.get_setting(session, key="alert_on_fail") == "1"
    if not alert_on_fail or skip_email:
        return

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
    
    send_email(subject, content, content_type="html")

def _handle_config_change_alert(session: Session, device: Device, record: BackupRecord, skip_email: bool = False):
    """
    处理配置变更告警
    """
    alert_on_change = crud.get_setting(session, key="alert_on_config_change") == "1"
    if not alert_on_change or skip_email:
        return

    # 获取上一个成功的备份记录
    backups = crud.list_device_backups(session, device.id, limit=2)
    # 过滤出成功的记录，排除当前这一条
    prev_success = None
    for b in backups:
        if b.id != record.id and b.success:
            prev_success = b
            break
    
    if not prev_success or not prev_success.config_text:
        return

    if record.config_text != prev_success.config_text:
        subject = f"【提醒】设备配置已变更: {device.name}({device.host})"
        
        # 构建 HTML 内容
        content = f"""
        <html>
        <body>
            <h3 style="color: #f0ad4e;">设备配置已变更</h3>
            <p>检测到配置与上一次成功备份相比发生了变更，请确认是否为预期操作。</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 800px;">
                <tr><th style="background-color: #f2f2f2; width: 120px; text-align: left;">设备名称</th><td>{device.name}</td></tr>
                <tr><th style="background-color: #f2f2f2; text-align: left;">设备地址</th><td>{device.host}</td></tr>
                <tr><th style="background-color: #f2f2f2; text-align: left;">变更时间</th><td>{_format_datetime(record.finished_at, session)}</td></tr>
            </table>
        </body>
        </html>
        """
        send_email(subject, content, content_type="html")

def check_and_alert_batch(session: Session, run_id: UUID):
    """
    检查批量备份任务并发送汇总告警邮件
    """
    run = crud.get_schedule_run(session, run_id)
    if not run:
        return

    # 检查是否有任何失败或变更
    # 获取该批次的所有记录
    items = crud.list_schedule_run_items(session, run_id)
    if not items:
        return

    backup_ids = [item.backup_id for item in items]
    records = crud.list_backups_by_ids(session, backup_ids)
    
    failed_records = []
    cancelled_records = []
    changed_records = []
    
    # 获取配置
    alert_on_fail = crud.get_setting(session, key="alert_on_fail") == "1"
    alert_on_change = crud.get_setting(session, key="alert_on_config_change") == "1"
    always_send = crud.get_setting(session, key="always_send_summary") == "1"

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
            if prev_success and prev_success.config_text and record.config_text != prev_success.config_text:
                changed_records.append((device, record))

    # 决定是否发送邮件
    # 1. 如果开启了“始终发送汇总报告”，则发送
    # 2. 如果未开启汇总，但开启了“失败告警”且有失败，则发送
    # 3. 如果未开启汇总，但开启了“变更提醒”且有变更，则发送
    should_send = always_send
    if not should_send and alert_on_fail and (failed_records or cancelled_records):
        should_send = True
    if not should_send and alert_on_change and changed_records:
        should_send = True

    if not should_send:
        return

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
                <th style="text-align: left; width: 50%;">设备名称</th>
                <th style="text-align: left; width: 50%;">设备地址</th>
            </tr>
        """
        for device, record in changed_records:
            content += f"""
            <tr>
                <td>{device.name}</td>
                <td>{device.host}</td>
            </tr>
            """
        content += "</table><br>"

    content += """
    </body>
    </html>
    """

    send_email(subject, content, content_type="html")
