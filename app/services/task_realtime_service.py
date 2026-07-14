from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from app.db import session_scope
from app.core.time import format_local_datetime
from app.models import TaskEvent
from app.services import backup_service, task_event_bus_service, task_state_service
from app.i18n import has_key, translate
from app.i18n.validators import normalize_locale


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskRealtimeSubscription:
    kind: str
    track_id: str
    allowed_group_ids: tuple[int, ...] | None
    tz_offset_minutes: int
    locale: str = "zh-CN"

    @property
    def scope_key(self) -> str:
        if self.allowed_group_ids is None:
            groups_key = "*"
        else:
            groups_key = ",".join(str(x) for x in self.allowed_group_ids)
        return f"{self.kind}:{self.track_id}:tz:{self.tz_offset_minutes}:locale:{self.locale}:groups:{groups_key}"

    @property
    def track_key(self) -> str:
        return f"{self.kind}:{self.track_id}"


@dataclass(slots=True)
class TaskRealtimeConnection:
    connection_id: str
    websocket: WebSocket
    subscription: TaskRealtimeSubscription | None = None
    log_subscription: TaskRealtimeSubscription | None = None


def _normalize_allowed_group_ids(value: list[int] | tuple[int, ...] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    return tuple(sorted({int(x) for x in value}))


def build_subscription(
    *,
    run_id: UUID | str | None = None,
    backup_id: UUID | str | None = None,
    allowed_group_ids: list[int] | tuple[int, ...] | None,
    tz_offset_minutes: int,
    locale: str = "zh-CN",
) -> TaskRealtimeSubscription:
    if bool(run_id) == bool(backup_id):
        raise ValueError("subscribe requires exactly one of run_id or backup_id")
    if run_id:
        return TaskRealtimeSubscription(
            kind="run",
            track_id=str(UUID(str(run_id))),
            allowed_group_ids=_normalize_allowed_group_ids(allowed_group_ids),
            tz_offset_minutes=int(tz_offset_minutes or 0),
            locale=normalize_locale(locale),
        )
    return TaskRealtimeSubscription(
        kind="backup",
        track_id=str(UUID(str(backup_id))),
        allowed_group_ids=_normalize_allowed_group_ids(allowed_group_ids),
        tz_offset_minutes=int(tz_offset_minutes or 0),
        locale=normalize_locale(locale),
    )


def _build_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(items),
        "planned": 0,
        "queued": 0,
        "running": 0,
        "success": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for item in items:
        status = str(item.get("status") or "").strip()
        if status == task_state_service.BACKUP_RECORD_STATUS_PLANNED:
            summary["planned"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_QUEUED:
            summary["queued"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_RUNNING:
            summary["running"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_CANCELLED:
            summary["cancelled"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED:
            summary["success"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_FAILED:
            summary["failed"] += 1
    return summary


def _load_subscription_snapshot(subscription: TaskRealtimeSubscription) -> dict[str, Any]:
    allowed_group_ids = None if subscription.allowed_group_ids is None else list(subscription.allowed_group_ids)
    offset_minutes = int(subscription.tz_offset_minutes or 0)
    with session_scope() as session:
        if subscription.kind == "run":
            raw = backup_service.list_task_backups_for_run(
                session,
                run_id=UUID(subscription.track_id),
                offset_minutes=offset_minutes,
                allowed_group_ids=allowed_group_ids,
            )
            found = bool(raw.get("found"))
            items = list(raw.get("items") or [])
            run_status = str(raw.get("run_status") or "")
            active = task_state_service.is_schedule_run_active_status(run_status) or any(
                task_state_service.is_backup_record_active_status(str(item.get("status") or ""))
                for item in items
            )
            return {
                "type": "task_snapshot",
                "track": {"kind": "run", "id": subscription.track_id},
                "found": found,
                "run_id": subscription.track_id,
                "run_status": run_status,
                "items": items,
                "running": int(raw.get("running") or 0),
                "summary": _build_summary(items),
                "active": bool(active),
            }

        raw = backup_service.get_task_backup(
            session,
            backup_id=UUID(subscription.track_id),
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
        items = list(raw.get("items") or [])
        return {
            "type": "task_snapshot",
            "track": {"kind": "backup", "id": subscription.track_id},
            "found": bool(raw.get("found")),
            "backup_id": subscription.track_id,
            "items": items,
            "running": int(raw.get("running") or 0),
            "summary": _build_summary(items),
            "active": any(
                task_state_service.is_backup_record_active_status(str(item.get("status") or ""))
                for item in items
            ),
        }


def _task_event_message_legacy(event: TaskEvent, details: dict[str, Any]) -> tuple[str, str]:
    event_name = str(event.event or "").strip()
    error = str(details.get("error") or "").strip()
    storage_type = str(event.storage_type or details.get("storage_type") or "").strip()
    failure_type = str(event.failure_type or details.get("failure_type") or "").strip()
    host = str(details.get("host") or "").strip()
    port = str(details.get("port") or "").strip()
    login_method = str(details.get("login_method") or "").strip()
    command = str(details.get("command") or "").strip()
    command_index = int(details.get("command_index") or 0)
    command_count = int(details.get("command_count") or 0)
    duration_seconds = details.get("duration_seconds")
    content_bytes = int(details.get("content_bytes") or details.get("output_bytes") or 0)

    def format_bytes(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.2f} MB"
        if value >= 1024:
            return f"{value / 1024:.1f} KB"
        return f"{value} B"

    def command_prefix() -> str:
        if command_index and command_count:
            return f"[{command_index}/{command_count}] "
        if command_index:
            return f"[{command_index}] "
        return ""

    def format_duration_text(value: Any) -> str:
        raw_text = str(details.get("duration_text") or "").strip()
        if raw_text:
            return raw_text
        if value is None or value == "":
            return ""
        try:
            seconds = max(0, int(float(value)))
        except (TypeError, ValueError):
            return ""
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {sec}s"
        hours, minute = divmod(minutes, 60)
        return f"{hours}h {minute}m {sec}s"

    if event_name == "backup_record_task_started":
        return ("info", "开始执行备份任务")
    if event_name == "backup_record_connection_started":
        target = host or "设备"
        if port:
            target = f"{target}:{port}"
        method_suffix = f" ({login_method})" if login_method else ""
        return ("info", f"开始连接 {target}{method_suffix}")
    if event_name == "backup_record_netmiko_connecting":
        target = host or "设备"
        if port:
            target = f"{target}:{port}"
        device_type = str(details.get("device_type") or "").strip()
        timeout = str(details.get("conn_timeout") or "").strip()
        return ("info", f"建立 Netmiko 会话: {target}，驱动 {device_type or '-'}，连接超时时间为 {timeout or '-'} 秒")
    if event_name == "backup_record_netmiko_connected":
        return ("success", "设备会话已建立")
    if event_name == "backup_record_enable_started":
        return ("info", "进入特权模式")
    if event_name == "backup_record_enable_completed":
        return ("success", "特权模式已就绪")
    if event_name == "backup_record_prompt_detected":
        prompt = str(details.get("prompt") or "").strip()
        return ("info", f"识别设备提示符: {prompt or '-'}")
    if event_name == "backup_record_command_started":
        timeout = int(details.get("read_timeout") or 0)
        suffix = f"，读取超时时间为 {timeout} 秒" if timeout else ""
        return ("info", f"执行命令 {command_prefix()}{command or '-'}{suffix}")
    if event_name == "backup_record_command_pagination_detected":
        return ("warning", f"检测到分页输出，切换分页处理: {command_prefix()}{command or '-'}")
    if event_name == "backup_record_command_pagination_completed":
        page_reads = int(details.get("page_reads") or 0)
        return ("info", f"分页处理完成: {command_prefix()}{command or '-'}，翻页 {page_reads} 次，输出 {format_bytes(content_bytes)}")
    if event_name == "backup_record_command_completed":
        line_count = int(details.get("line_count") or 0)
        duration_text = f"，耗时 {duration_seconds}s" if duration_seconds is not None else ""
        return ("success", f"命令完成 {command_prefix()}{command or '-'}，输出 {line_count} 行 / {format_bytes(content_bytes)}{duration_text}")
    if event_name == "backup_record_netmiko_disconnected":
        return ("info", "设备会话已断开")
    if event_name == "backup_record_legacy_ssh_fallback_started":
        return ("warning", "检测到 SSH 算法兼容问题，尝试旧算法兼容模式")
    if event_name == "backup_record_legacy_ssh_fallback_completed":
        return ("success", "旧算法兼容模式连接成功")
    if event_name == "backup_record_netmiko_failed":
        return ("error", f"设备交互失败: {error or '未知错误'}")
    if event_name == "backup_record_netmiko_completed":
        duration_text = f"，耗时 {duration_seconds}s" if duration_seconds is not None else ""
        return ("success", f"设备命令采集完成{duration_text}")
    if event_name == "backup_record_collection_completed":
        line_count = int(details.get("line_count") or 0)
        content_bytes = int(details.get("content_bytes") or 0)
        if line_count > 0:
            return ("info", f"配置采集完成，共 {line_count} 行")
        if content_bytes > 0:
            return ("info", f"配置采集完成，大小 {content_bytes} 字节")
        return ("info", "配置采集完成")
    if event_name == "backup_record_storage_upload_started" and content_bytes:
        label = storage_type or "远端存储"
        if label.upper() == "FTP":
            target = str(details.get("host") or "").strip()
            target_port = str(details.get("port") or "").strip()
            base_dir = str(details.get("base_dir") or "").strip().strip("/")
            passive = str(details.get("passive") or "").strip()
            target_text = f"{target}:{target_port}" if target and target_port else target or "FTP"
            base_text = f"，目录 /{base_dir}" if base_dir else ""
            passive_text = f"，被动模式 {passive}" if passive else ""
            return ("info", f"开始上传到 FTP: {target_text}{base_text}{passive_text}，数据 {format_bytes(content_bytes)}")
        if label.upper() == "S3":
            bucket = str(details.get("bucket") or "").strip()
            prefix = str(details.get("prefix") or "").strip().strip("/")
            endpoint = str(details.get("endpoint") or "").strip()
            target_text = bucket or "S3"
            prefix_text = f"，前缀 {prefix}" if prefix else ""
            endpoint_text = f"，端点 {endpoint}" if endpoint else ""
            return ("info", f"开始上传到 S3: {target_text}{prefix_text}{endpoint_text}，数据 {format_bytes(content_bytes)}")
        return ("info", f"开始上传到 {label}，数据 {format_bytes(content_bytes)}")
    if event_name == "backup_record_storage_upload_started":
        label = storage_type or "远端存储"
        return ("info", f"开始上传到 {label}")
    if event_name == "backup_record_task_succeeded":
        return ("success", "备份完成")
    if event_name == "backup_record_task_failed":
        return ("error", f"备份失败: {error or failure_type or '未知错误'}")
    if event_name == "backup_record_task_retry_scheduled":
        retries_done = int(details.get("retries_done") or event.retries_done or 0)
        max_retries = int(details.get("max_retries") or event.max_retries or 0)
        return ("warning", f"任务将重试 ({retries_done + 1}/{max_retries or retries_done + 1})")
    if event_name == "backup_record_semaphore_degraded":
        retries_done = int(details.get("retries_done") or event.retries_done or 0)
        retry_countdown = int(details.get("retry_countdown") or 5)
        if failure_type == "REDIS_SEMAPHORE_FULL" or str(details.get("reason") or "") == "redis_semaphore_full":
            max_slots = int(details.get("max_slots") or 0)
            limit_text = f" (limit {max_slots})" if max_slots else ""
            return ("warning", f"Backup concurrency limit reached{limit_text}; retrying in {retry_countdown}s, retry #{retries_done + 1}")
        fail_open = bool(details.get("fail_open"))
        policy = "fail-open" if fail_open else "fail-closed"
        return ("warning", f"Redis semaphore degraded ({policy}); retrying backup in {retry_countdown}s, retry #{retries_done + 1}")
    if event_name == "backup_record_storage_upload" and content_bytes:
        ok = bool(event.success if event.success is not None else details.get("success"))
        label = storage_type or "远端存储"
        hint = "" if ok else "，请检查存储配置、网络连通性和目录权限"
        return ("success" if ok else "warning", f"{label} 上传{'成功' if ok else '失败'}，数据 {format_bytes(content_bytes)}{hint}")
    if event_name == "backup_record_storage_upload":
        ok = bool(event.success if event.success is not None else details.get("success"))
        label = storage_type or "远端存储"
        return ("success" if ok else "warning", f"{label} 上传{'成功' if ok else '失败'}")
    if event_name == "backup_record_alert_check_started":
        return ("info", "开始检查告警与通知条件")
    if event_name == "backup_record_alert_check_completed":
        mode = str(details.get("mode") or "").strip()
        reason = str(details.get("reason") or "").strip()
        error_detail = str(details.get("error") or "").strip()
        rule_enabled = bool(details.get("rule_enabled"))
        matched = bool(details.get("matched"))
        email_attempted = bool(details.get("email_attempted"))
        email_sent = bool(details.get("email_sent"))
        skipped = bool(details.get("skipped"))
        mode_label_map = {
            "failure": "失败告警",
            "config_change": "配置变更提醒",
            "single_summary": "单设备汇总通知",
            "device_missing": "设备检查",
        }
        reason_label_map = {
            "skip_email": "批量任务已跳过单设备邮件，等待批次汇总通知",
            "rule_disabled": "规则未开启",
            "failure_rule_matched": "失败告警规则已命中",
            "config_changed": "配置变更规则已命中",
            "no_config_change": "未检测到配置变更",
            "always_send_summary": "已启用总是发送汇总",
            "smtp_incomplete": "邮件未发送，SMTP 配置不完整",
            "email_send_failed": "邮件发送失败",
            "device_missing": "设备不存在，已跳过",
        }
        mode_label = mode_label_map.get(mode, "告警与通知")
        reason_label = reason_label_map.get(reason, reason or "")
        if email_attempted:
            if email_sent:
                return ("success", f"{mode_label}检查完成: {reason_label or '规则已命中'}，邮件发送成功")
            suffix = f"，{error_detail}" if error_detail else ""
            return ("warning", f"{mode_label}检查完成: {reason_label or '邮件未发送'}{suffix}")
        if not rule_enabled:
            return ("info", f"{mode_label}检查完成: 规则未开启，未发送通知")
        if skipped:
            return ("info", f"{mode_label}检查完成: {reason_label or '已跳过'}")
        if not matched:
            return ("info", f"{mode_label}检查完成: {reason_label or '未命中规则'}，未发送通知")
        return ("info", f"{mode_label}检查完成: {reason_label or '已完成'}")
    if event_name == "backup_record_alert_check_completed":
        return ("info", "告警与通知检查完成")
    if event_name == "backup_record_task_aborted":
        return ("warning", f"任务已中止: {failure_type or error or '执行上下文不可用'}")
    if event_name == "backup_record_task_signal_failure":
        return ("error", f"任务异常失败: {error or failure_type or '未知错误'}")
    if event_name == "backup_record_task_revoked":
        return ("warning", f"任务被撤销: {error or '已终止'}")
    if event_name == "schedule_run_planned":
        planned_count = int(details.get("planned_count") or details.get("total_devices") or 0)
        trigger = str(details.get("trigger") or "").strip()
        schedule_id = int(details.get("schedule_id") or 0)
        suffix = f"，计划 ID {schedule_id}" if schedule_id else ""
        trigger_text = f"，触发方式 {trigger}" if trigger else ""
        return ("info", f"批次已创建，计划备份 {planned_count} 台设备{suffix}{trigger_text}")
    if event_name == "schedule_run_dispatch_started":
        job_count = int(details.get("job_count") or 0)
        return ("info", f"开始批次入队，待调度 {job_count} 个任务")
    if event_name == "schedule_run_finalizer_scheduled":
        backup_count = int(details.get("backup_count") or 0)
        poll_seconds = int(details.get("poll_seconds") or 0)
        suffix = f"，每 {poll_seconds} 秒检查一次" if poll_seconds else ""
        return ("info", f"已安排批次收尾检查，跟踪 {backup_count} 个任务{suffix}")
    if event_name == "schedule_run_dispatch_completed":
        enqueued_count = int(details.get("enqueued_count") or 0)
        job_count = int(details.get("job_count") or enqueued_count)
        return ("success", f"批次入队完成，已提交 {enqueued_count}/{job_count} 个任务")
    if event_name == "schedule_run_dispatch_partial":
        enqueued_count = int(details.get("enqueued_count") or 0)
        failed_count = int(details.get("failed_count") or 0)
        return ("warning", f"批次部分入队成功，已提交 {enqueued_count} 个，失败 {failed_count} 个")
    if event_name == "schedule_run_dispatch_failed":
        failed_count = int(details.get("failed_count") or details.get("job_count") or 0)
        reason = str(details.get("reason") or error or failure_type or "").strip()
        suffix = f": {reason}" if reason else ""
        return ("error", f"批次入队失败，失败 {failed_count} 个任务{suffix}")
    if event_name == "schedule_run_terminate_requested":
        terminated = int(details.get("terminated_records") or 0)
        running = int(details.get("running_records") or 0)
        return ("warning", f"已终止 {terminated} 个未运行任务，仍有 {running} 个运行中任务等待完成")
    if event_name == "schedule_run_terminate_completed":
        terminated = int(details.get("terminated_records") or 0)
        skipped = int(details.get("skipped_records") or 0)
        return ("warning", f"批次终止完成，终止 {terminated} 个，跳过 {skipped} 个")
    if event_name == "schedule_run_terminate_skipped":
        running = int(details.get("running_records") or 0)
        return ("warning", f"没有可终止的未运行任务，当前运行中 {running} 个")
    if event_name == "schedule_run_terminate_selected_completed":
        selected = int(details.get("selected_records") or 0)
        terminated = int(details.get("terminated_records") or 0)
        skipped = int(details.get("skipped_records") or 0)
        return ("warning", f"选中任务终止完成，已选 {selected} 个，终止 {terminated} 个，跳过 {skipped} 个")
    if event_name == "schedule_run_terminate_selected_skipped":
        selected = int(details.get("selected_records") or 0)
        running = int(details.get("running_records") or 0)
        return ("warning", f"选中的 {selected} 个任务中没有可终止项，运行中 {running} 个")
    if event_name == "schedule_run_retry_created":
        retried = int(details.get("retried_records") or 0)
        skipped = int(details.get("skipped_records") or 0)
        enqueued = int(details.get("enqueued_count") or 0)
        return ("info", f"已创建重试批次，重试 {retried} 个，入队 {enqueued} 个，跳过 {skipped} 个")
    if event_name == "schedule_run_retry_selected_created":
        selected = int(details.get("selected_records") or 0)
        retried = int(details.get("retried_records") or 0)
        enqueued = int(details.get("enqueued_count") or 0)
        return ("info", f"已创建选中任务重试批次，已选 {selected} 个，重试 {retried} 个，入队 {enqueued} 个")
    if event_name == "schedule_run_alert_check_started":
        success_count = int(details.get("success_count") or 0)
        fail_count = int(details.get("fail_count") or 0)
        cancelled_count = int(details.get("cancelled_count") or 0)
        return ("info", f"开始检查批次汇总通知条件，成功 {success_count}，失败 {fail_count}，终止 {cancelled_count}")
    if event_name == "schedule_run_alert_check_completed":
        failed_count = int(details.get("failed_count") or details.get("fail_count") or 0)
        cancelled_count = int(details.get("cancelled_count") or 0)
        changed_count = int(details.get("changed_count") or 0)
        rule_enabled = bool(details.get("rule_enabled"))
        email_attempted = bool(details.get("email_attempted"))
        email_sent = bool(details.get("email_sent"))
        skipped = bool(details.get("skipped"))
        reason = str(details.get("reason") or "").strip()
        trigger_reason = str(details.get("trigger_reason") or reason).strip()
        error_detail = str(details.get("error") or "").strip()
        trigger_label_map = {
            "always_send_summary": "已启用总是发送汇总",
            "failure_rule_matched": "失败/终止告警规则已命中",
            "config_changed": "配置变更提醒规则已命中",
            "no_rule_matched": "未命中通知规则",
            "rule_disabled": "规则未开启",
        }
        trigger_label = trigger_label_map.get(trigger_reason, trigger_reason or "")
        if email_attempted:
            if reason == "email_send_failed":
                suffix = f"，{error_detail}" if error_detail else ""
                return ("warning", f"批次汇总通知检查完成: 邮件发送失败{suffix}")
            if reason == "smtp_incomplete":
                return ("warning", "批次汇总通知检查完成: SMTP 配置不完整，邮件未发送成功")
            return (
                "success" if email_sent else "warning",
                f"批次汇总通知检查完成: {trigger_label or '通知规则已命中'}，邮件{'发送成功' if email_sent else '未发送成功'}",
            )
        if not rule_enabled:
            return ("info", "批次汇总通知检查完成: 规则未开启，未发送通知")
        if skipped and reason == "no_rule_matched":
            if not failed_count and not cancelled_count and not changed_count:
                return ("info", "批次汇总通知检查完成: 未检测到失败、终止或配置变更，未发送通知")
            return ("info", "批次汇总通知检查完成: 未命中通知规则，未发送通知")
        return ("info", f"批次汇总通知检查完成: {trigger_label or '已完成检查'}")
    if event_name == "finalize_schedule_run_started":
        backup_count = int(details.get("backup_count") or 0)
        suffix = f"，跟踪 {backup_count} 个任务" if backup_count else ""
        return ("info", f"开始批次收尾检查{suffix}，等待设备任务完成后统计结果")
    if event_name == "finalize_schedule_run_started":
        return ("info", "批次进入收尾阶段")
    if event_name == "finalize_schedule_run_completed":
        success_count = int(details.get("success_count") or 0)
        fail_count = int(details.get("fail_count") or 0)
        duration = format_duration_text(details.get("duration_seconds"))
        duration_suffix = f"，总耗时 {duration}" if duration else ""
        return ("info", f"批次收尾完成，成功 {success_count}，失败 {fail_count}{duration_suffix}")
    if event_name == "finalize_schedule_run_missing":
        return ("warning", "批次收尾时未找到运行记录")
    if event_name == "finalize_schedule_run_skipped":
        return ("info", "批次已完成，跳过重复收尾")
    return ("info", error or event_name or "任务事件")


def _task_event_message(event: TaskEvent, details: dict[str, Any], *, locale: str = "zh-CN") -> tuple[str, str]:
    tone, fallback = _task_event_message_legacy(event, details)
    event_name = str(event.event or "").strip()
    key = f"task.event.{event_name}"
    normalized = normalize_locale(locale)
    if has_key(key, normalized):
        def event_text(message_key: str, params: dict[str, Any] | None = None) -> str:
            return translate(normalized, f"task.param.{message_key}", params)

        params = dict(details)
        host = str(details.get("host") or "").strip()
        port = str(details.get("port") or "").strip()
        target = host or event_text("device")
        if port:
            target = f"{target}:{port}"
        login_method = str(details.get("login_method") or "").strip()
        command = str(details.get("command") or "-").strip() or "-"
        command_index = int(details.get("command_index") or 0)
        command_count = int(details.get("command_count") or 0)
        if command_index and command_count:
            command = f"[{command_index}/{command_count}] {command}"
        elif command_index:
            command = f"[{command_index}] {command}"
        read_timeout = int(details.get("read_timeout") or 0)
        content_bytes = int(details.get("content_bytes") or details.get("output_bytes") or 0)
        if content_bytes >= 1024 * 1024:
            content_size = f"{content_bytes / (1024 * 1024):.2f} MB"
        elif content_bytes >= 1024:
            content_size = f"{content_bytes / 1024:.1f} KB"
        else:
            content_size = f"{content_bytes} B"
        duration_seconds = details.get("duration_seconds")
        line_count = int(details.get("line_count") or 0)
        storage_label = str(event.storage_type or details.get("storage_type") or "").strip() or event_text("remote_storage")
        storage_target = storage_label
        storage_options = ""
        if storage_label.upper() == "FTP":
            ftp_host = str(details.get("host") or "").strip()
            ftp_port = str(details.get("port") or "").strip()
            storage_target = f"FTP: {ftp_host}:{ftp_port}" if ftp_host and ftp_port else f"FTP: {ftp_host or 'FTP'}"
            base_dir = str(details.get("base_dir") or "").strip().strip("/")
            passive = str(details.get("passive") or "").strip()
            storage_options = event_text("directory_option", {"value": f"/{base_dir}"}) if base_dir else ""
            storage_options += event_text("passive_option", {"value": passive}) if passive else ""
        elif storage_label.upper() == "S3":
            bucket = str(details.get("bucket") or "").strip()
            prefix = str(details.get("prefix") or "").strip().strip("/")
            endpoint = str(details.get("endpoint") or "").strip()
            storage_target = f"S3: {bucket or 'S3'}"
            storage_options = event_text("prefix_option", {"value": prefix}) if prefix else ""
            storage_options += event_text("endpoint_option", {"value": endpoint}) if endpoint else ""
        upload_ok = bool(event.success if event.success is not None else details.get("success"))
        effective_error = str(details.get("error") or event.failure_type or details.get("failure_type") or "").strip()
        retries_done = int(details.get("retries_done") or event.retries_done or 0)
        max_retries = int(details.get("max_retries") or event.max_retries or 0)
        planned_count = int(details.get("planned_count") or details.get("total_devices") or 0)
        schedule_id = int(details.get("schedule_id") or 0)
        trigger = str(details.get("trigger") or "").strip()
        job_count = int(details.get("job_count") or 0)
        enqueued_count = int(details.get("enqueued_count") or 0)
        terminated_records = int(details.get("terminated_records") or 0)
        running_records = int(details.get("running_records") or 0)
        skipped_records = int(details.get("skipped_records") or 0)
        retried_records = int(details.get("retried_records") or 0)
        backup_count = int(details.get("backup_count") or 0)
        success_count = int(details.get("success_count") or 0)
        fail_count = int(details.get("fail_count") or 0)
        duration_text = str(details.get("duration_text") or "").strip()
        if not duration_text and duration_seconds is not None:
            duration_text = str(duration_seconds)
        params.update(
            {
                "target": target,
                "login_method_suffix": f" ({login_method})" if login_method else "",
                "device_type": str(details.get("device_type") or "-").strip() or "-",
                "conn_timeout": str(details.get("conn_timeout") or "-").strip() or "-",
                "command": command,
                "read_timeout_suffix": (
                    event_text("read_timeout_suffix", {"value": read_timeout}) if read_timeout else ""
                ),
                "content_size": content_size,
                "duration_suffix": (
                    event_text("duration_suffix", {"value": duration_seconds})
                    if duration_seconds is not None else ""
                ),
                "line_count": line_count,
                "collection_summary": (
                    event_text("line_count_suffix", {"value": line_count}) if line_count
                    else event_text("size_suffix", {"value": content_size}) if content_bytes
                    else ""
                ),
                "storage_label": storage_label,
                "storage_target": storage_target,
                "storage_options": storage_options,
                "content_size_suffix": (
                    event_text("content_size_suffix", {"value": content_size}) if content_bytes else ""
                ),
                "upload_result": event_text("upload_succeeded" if upload_ok else "upload_failed"),
                "upload_failure_hint": (
                    event_text("upload_failure_hint") if not upload_ok else ""
                ),
                "error": effective_error or event_text("unknown_error"),
                "retry_position": f"{retries_done + 1}/{max_retries or retries_done + 1}",
                "planned_count": planned_count,
                "schedule_suffix": (
                    event_text("schedule_suffix", {"value": schedule_id}) if schedule_id else ""
                ),
                "trigger_suffix": (
                    event_text("trigger_suffix", {"value": trigger}) if trigger else ""
                ),
                "job_count": job_count,
                "enqueued_count": enqueued_count,
                "failed_count": int(details.get("failed_count") or job_count or 0),
                "reason_suffix": (
                    f": {str(details.get('reason') or effective_error).strip()}"
                    if str(details.get("reason") or effective_error).strip()
                    else ""
                ),
                "terminated_records": terminated_records,
                "running_records": running_records,
                "skipped_records": skipped_records,
                "retried_records": retried_records,
                "backup_count_suffix": (
                    event_text("backup_count_suffix", {"value": backup_count}) if backup_count else ""
                ),
                "success_count": success_count,
                "fail_count": fail_count,
                "total_duration_suffix": (
                    event_text("total_duration_suffix", {"value": duration_text}) if duration_text else ""
                ),
            }
        )
        return tone, translate(normalized, key, params, fallback=fallback)
    return tone, translate(
        normalized,
        "task.event.unknown",
        {"event": event_name or "-", "error": str(details.get("error") or "").strip()},
        fallback=fallback,
    )


def _serialize_task_event(event: TaskEvent, *, offset_minutes: int, locale: str = "zh-CN") -> dict[str, Any]:
    try:
        details = json.loads(event.details or "{}")
        if not isinstance(details, dict):
            details = {}
    except Exception:
        details = {}
    tone, message = _task_event_message(event, details, locale=locale)
    return {
        "id": int(event.id or 0),
        "event": str(event.event or ""),
        "tone": tone,
        "message": message,
        "message_key": f"task.event.{str(event.event or '').strip()}",
        "message_params": details,
        "created_at": format_local_datetime(event.created_at, offset_minutes=offset_minutes),
        "task_id": str(event.task_id or ""),
        "record_id": str(event.record_id or ""),
        "run_id": str(event.run_id or ""),
        "failure_type": str(event.failure_type or ""),
        "details": details,
    }


def _should_hide_device_log_event(item: dict[str, Any]) -> bool:
    event_name = str(item.get("event") or "").strip()
    if event_name not in {"backup_record_alert_check_started", "backup_record_alert_check_completed"}:
        return False
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return bool(details.get("skip_email"))


def _parse_event_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _serialize_broadcast_task_event(payload: dict[str, Any], *, offset_minutes: int, locale: str = "zh-CN") -> dict[str, Any]:
    details_raw = payload.get("details")
    details = details_raw if isinstance(details_raw, dict) else {}
    task_event = TaskEvent(
        id=int(payload.get("event_id") or 0) or None,
        event=str(payload.get("event") or ""),
        task_id=str(payload.get("task_id") or "") or None,
        record_id=str(payload.get("record_id") or "") or None,
        run_id=str(payload.get("run_id") or "") or None,
        request_id=str(payload.get("request_id") or "") or None,
        device_id=int(payload.get("device_id") or 0) if payload.get("device_id") not in (None, "") else None,
        failure_type=str(payload.get("failure_type") or "") or None,
        storage_type=str(payload.get("storage_type") or "") or None,
        success=payload.get("success") if isinstance(payload.get("success"), bool) else None,
        retries_done=int(payload.get("retries_done") or 0) if payload.get("retries_done") not in (None, "") else None,
        max_retries=int(payload.get("max_retries") or 0) if payload.get("max_retries") not in (None, "") else None,
        created_at=_parse_event_created_at(payload.get("created_at")),
        details=json.dumps(details, ensure_ascii=False) if details else None,
    )
    serialized = _serialize_task_event(task_event, offset_minutes=offset_minutes, locale=locale)
    if not serialized.get("id"):
        serialized["id"] = int(payload.get("event_id") or 0)
    return serialized


def _extract_visible_record_ids(snapshot: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in (snapshot.get("items") or [])
        if item and item.get("id")
    }


def _load_log_payload(
    subscription: TaskRealtimeSubscription,
    *,
    after_id: int | None = None,
    initial_limit: int = 50,
    update_limit: int = 100,
) -> dict[str, Any]:
    from app.services import task_observability_service

    task_observability_service.flush_task_event_buffer(force=True)
    allowed_group_ids = None if subscription.allowed_group_ids is None else list(subscription.allowed_group_ids)
    offset_minutes = int(subscription.tz_offset_minutes or 0)
    with session_scope() as session:
        if subscription.kind == "run":
            raw = backup_service.list_task_backups_for_run(
                session,
                run_id=UUID(subscription.track_id),
                offset_minutes=offset_minutes,
                allowed_group_ids=allowed_group_ids,
            )
            found = bool(raw.get("found"))
            stmt = select(TaskEvent).where(
                TaskEvent.run_id == subscription.track_id,
                TaskEvent.record_id.is_(None),
            )
        else:
            raw = backup_service.get_task_backup(
                session,
                backup_id=UUID(subscription.track_id),
                offset_minutes=offset_minutes,
                allowed_group_ids=allowed_group_ids,
            )
            found = bool(raw.get("found"))
            stmt = select(TaskEvent).where(TaskEvent.record_id == subscription.track_id)

        if not found:
            return {
                "type": "task_logs",
                "track": {"kind": subscription.kind, "id": subscription.track_id},
                "found": False,
                "items": [],
                "next_after_id": int(after_id or 0),
                "reset": after_id is None,
            }

        limit = int(update_limit if after_id is not None else initial_limit)
        if after_id is not None:
            stmt = stmt.where(TaskEvent.id > int(after_id)).order_by(TaskEvent.id.asc()).limit(limit)
            rows = list(session.exec(stmt))
        else:
            rows = list(session.exec(stmt.order_by(TaskEvent.id.desc()).limit(limit)))
            rows.reverse()
        serialized_items = [
            _serialize_task_event(row, offset_minutes=offset_minutes, locale=subscription.locale)
            for row in rows
        ]
        if subscription.kind == "backup":
            items = [item for item in serialized_items if not _should_hide_device_log_event(item)]
        else:
            items = serialized_items
        next_after_id = int(rows[-1].id or after_id or 0) if rows else int(after_id or 0)
        return {
            "type": "task_logs",
            "track": {"kind": subscription.kind, "id": subscription.track_id},
            "found": True,
            "items": items,
            "next_after_id": next_after_id,
            "reset": after_id is None,
        }


class TaskRealtimeHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, TaskRealtimeConnection] = {}
        self._scope_connections: dict[str, set[str]] = {}
        self._scope_subscriptions: dict[str, TaskRealtimeSubscription] = {}
        self._scope_visible_record_ids: dict[str, set[str]] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._last_payloads: dict[str, str] = {}
        self._log_watchers: dict[str, asyncio.Task[None]] = {}
        self._event_bus_stop = asyncio.Event()
        self._event_bus_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not task_event_bus_service.event_bus_enabled():
            return
        async with self._lock:
            if self._event_bus_task is not None and not self._event_bus_task.done():
                return
            self._event_bus_stop = asyncio.Event()
            self._event_bus_task = asyncio.create_task(
                task_event_bus_service.pump_task_events(self.handle_event_bus_payload, stop_event=self._event_bus_stop),
                name="task-event-bus",
            )

    async def register(self, websocket: WebSocket) -> str:
        connection_id = uuid4().hex
        async with self._lock:
            self._connections[connection_id] = TaskRealtimeConnection(
                connection_id=connection_id,
                websocket=websocket,
            )
        return connection_id

    async def unregister(self, connection_id: str) -> None:
        async with self._lock:
            connection = self._connections.pop(connection_id, None)
            if connection is None:
                return
            self._detach_subscription_locked(connection_id, connection)
            self._detach_log_subscription_locked(connection_id, connection)

    async def clear_subscription(self, connection_id: str) -> None:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return
            self._detach_subscription_locked(connection_id, connection)
            self._detach_log_subscription_locked(connection_id, connection)

    async def subscribe(self, connection_id: str, subscription: TaskRealtimeSubscription) -> dict[str, Any]:
        watcher_to_start: str | None = None
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise RuntimeError("connection not found")
            if connection.subscription is not None:
                self._detach_subscription_locked(connection_id, connection)
            if connection.log_subscription is not None and connection.log_subscription.track_key != subscription.track_key:
                self._detach_log_subscription_locked(connection_id, connection)
            connection.subscription = subscription
            conn_ids = self._scope_connections.setdefault(subscription.scope_key, set())
            conn_ids.add(connection_id)
            self._scope_subscriptions[subscription.scope_key] = subscription
            if not task_event_bus_service.event_bus_enabled():
                watcher = self._watchers.get(subscription.scope_key)
                if watcher is None or watcher.done():
                    watcher_to_start = subscription.scope_key

        snapshot = await run_in_threadpool(_load_subscription_snapshot, subscription)
        await self._cache_scope_snapshot(subscription, snapshot)
        if watcher_to_start:
            async with self._lock:
                watcher = self._watchers.get(subscription.scope_key)
                if watcher is None or watcher.done():
                    self._watchers[subscription.scope_key] = asyncio.create_task(
                        self._watch_scope(subscription),
                        name=f"task-realtime:{subscription.scope_key}",
                    )
        await self._send_to_connection(
            connection_id,
            {
                "type": "task_subscribed",
                "track": {"kind": subscription.kind, "id": subscription.track_id},
            },
        )
        await self._send_to_connection(connection_id, snapshot)
        return snapshot

    async def subscribe_logs(
        self,
        connection_id: str,
        subscription: TaskRealtimeSubscription,
        *,
        after_id: int | None = None,
    ) -> dict[str, Any]:
        watcher_to_start = False
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                raise RuntimeError("connection not found")
            self._detach_log_subscription_locked(connection_id, connection)
            connection.log_subscription = subscription
            if not task_event_bus_service.event_bus_enabled():
                watcher_to_start = True
                self._log_watchers[connection_id] = asyncio.create_task(
                    self._watch_logs(connection_id, subscription, after_id=after_id),
                    name=f"task-log:{connection_id}",
                )
        payload = await run_in_threadpool(_load_log_payload, subscription, after_id=after_id)
        await self._send_to_connection(
            connection_id,
            {
                "type": "task_logs_subscribed",
                "track": {"kind": subscription.kind, "id": subscription.track_id},
            },
        )
        await self._send_to_connection(connection_id, payload)
        return payload

    async def clear_log_subscription(self, connection_id: str) -> None:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return
            self._detach_log_subscription_locked(connection_id, connection)

    async def send(self, connection_id: str, payload: dict[str, Any]) -> None:
        await self._send_to_connection(connection_id, payload)

    def _detach_subscription_locked(self, connection_id: str, connection: TaskRealtimeConnection) -> None:
        if connection.subscription is None:
            return
        scope_key = connection.subscription.scope_key
        conn_ids = self._scope_connections.get(scope_key)
        if conn_ids is not None:
            conn_ids.discard(connection_id)
            if not conn_ids:
                self._scope_connections.pop(scope_key, None)
                self._scope_subscriptions.pop(scope_key, None)
                self._scope_visible_record_ids.pop(scope_key, None)
                self._last_payloads.pop(scope_key, None)
        connection.subscription = None

    def _detach_log_subscription_locked(self, connection_id: str, connection: TaskRealtimeConnection) -> None:
        watcher = self._log_watchers.pop(connection_id, None)
        if watcher is not None:
            watcher.cancel()
        connection.log_subscription = None

    async def _send_to_connection(self, connection_id: str, payload: dict[str, Any]) -> None:
        websocket: WebSocket | None = None
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return
            websocket = connection.websocket
        try:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.debug("Failed to send task realtime payload", exc_info=True)
            await self.unregister(connection_id)

    async def _broadcast(self, scope_key: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            conn_ids = list(self._scope_connections.get(scope_key, set()))
        if not conn_ids:
            return
        for connection_id in conn_ids:
            await self._send_to_connection(connection_id, payload)

    async def _cache_scope_snapshot(self, subscription: TaskRealtimeSubscription, payload: dict[str, Any]) -> None:
        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        visible_record_ids = _extract_visible_record_ids(payload)
        async with self._lock:
            self._last_payloads[subscription.scope_key] = payload_text
            self._scope_visible_record_ids[subscription.scope_key] = visible_record_ids

    async def _refresh_scope_from_event(self, scope_key: str, subscription: TaskRealtimeSubscription) -> None:
        try:
            payload = await run_in_threadpool(_load_subscription_snapshot, subscription)
        except Exception as exc:
            logger.warning("Task realtime snapshot load failed: %s", exc, exc_info=True)
            await self._broadcast(
                scope_key,
                {
                    "type": "task_error",
                    "track": {"kind": subscription.kind, "id": subscription.track_id},
                    "message": "任务状态同步失败",
                },
            )
            return
        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        async with self._lock:
            last_payload = self._last_payloads.get(scope_key)
        if payload_text == last_payload:
            return
        await self._cache_scope_snapshot(subscription, payload)
        await self._broadcast(scope_key, payload)

    async def _broadcast_log_event(self, payload: dict[str, Any]) -> None:
        record_id = str(payload.get("record_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        event_id = int(payload.get("event_id") or 0)
        targets: list[tuple[str, TaskRealtimeSubscription]] = []
        async with self._lock:
            for connection_id, connection in self._connections.items():
                subscription = connection.log_subscription
                if subscription is None:
                    continue
                if subscription.kind == "backup":
                    if record_id and subscription.track_id == record_id:
                        targets.append((connection_id, subscription))
                    continue
                if run_id and subscription.track_id == run_id and not record_id:
                    targets.append((connection_id, subscription))
        if not targets:
            return
        for connection_id, subscription in targets:
            item = _serialize_broadcast_task_event(
                payload,
                offset_minutes=subscription.tz_offset_minutes,
                locale=subscription.locale,
            )
            if subscription.kind == "backup" and _should_hide_device_log_event(item):
                continue
            next_after_id = int(item.get("id") or event_id or 0)
            await self._send_to_connection(
                connection_id,
                {
                    "type": "task_logs",
                    "track": {"kind": subscription.kind, "id": subscription.track_id},
                    "found": True,
                    "items": [item],
                    "next_after_id": next_after_id,
                    "reset": False,
                },
            )

    def _event_matches_scope(
        self,
        payload: dict[str, Any],
        subscription: TaskRealtimeSubscription,
        visible_record_ids: set[str],
    ) -> bool:
        record_id = str(payload.get("record_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if subscription.kind == "backup":
            return bool(record_id) and subscription.track_id == record_id
        if run_id and subscription.track_id == run_id:
            if not record_id:
                return True
            return not visible_record_ids or record_id in visible_record_ids
        return bool(record_id) and record_id in visible_record_ids

    async def handle_event_bus_payload(self, payload: dict[str, Any]) -> None:
        payload_type = str(payload.get("type") or "").strip()
        scope_targets: list[tuple[str, TaskRealtimeSubscription]] = []
        async with self._lock:
            for scope_key, subscription in self._scope_subscriptions.items():
                visible_record_ids = self._scope_visible_record_ids.get(scope_key, set())
                if self._event_matches_scope(payload, subscription, visible_record_ids):
                    scope_targets.append((scope_key, subscription))
        for scope_key, subscription in scope_targets:
            await self._refresh_scope_from_event(scope_key, subscription)
        if payload_type == "task_event":
            await self._broadcast_log_event(payload)

    async def _watch_scope(self, subscription: TaskRealtimeSubscription) -> None:
        scope_key = subscription.scope_key
        try:
            while True:
                async with self._lock:
                    if not self._scope_connections.get(scope_key):
                        self._last_payloads.pop(scope_key, None)
                        return

                try:
                    payload = await run_in_threadpool(_load_subscription_snapshot, subscription)
                except Exception as exc:
                    logger.warning("Task realtime snapshot load failed: %s", exc, exc_info=True)
                    await self._broadcast(
                        scope_key,
                        {
                            "type": "task_error",
                            "track": {"kind": subscription.kind, "id": subscription.track_id},
                            "message": "任务状态同步失败",
                        },
                    )
                    await asyncio.sleep(2.0)
                    continue

                payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if payload_text != self._last_payloads.get(scope_key):
                    await self._cache_scope_snapshot(subscription, payload)
                    await self._broadcast(scope_key, payload)

                sleep_seconds = 1.0 if payload.get("active") else 2.5
                await asyncio.sleep(sleep_seconds)
        finally:
            async with self._lock:
                watcher = self._watchers.get(scope_key)
                if watcher is asyncio.current_task():
                    self._watchers.pop(scope_key, None)

    async def _watch_logs(
        self,
        connection_id: str,
        subscription: TaskRealtimeSubscription,
        *,
        after_id: int | None = None,
    ) -> None:
        next_after_id = after_id
        try:
            while True:
                async with self._lock:
                    connection = self._connections.get(connection_id)
                    if connection is None or connection.log_subscription is None:
                        return
                    if connection.log_subscription.track_key != subscription.track_key:
                        return
                try:
                    payload = await run_in_threadpool(_load_log_payload, subscription, after_id=next_after_id)
                except Exception as exc:
                    logger.warning("Task realtime log load failed: %s", exc, exc_info=True)
                    await self._send_to_connection(
                        connection_id,
                        {
                            "type": "task_error",
                            "track": {"kind": subscription.kind, "id": subscription.track_id},
                            "message": "任务日志同步失败",
                        },
                    )
                    await asyncio.sleep(2.0)
                    continue
                items = list(payload.get("items") or [])
                if items:
                    next_after_id = int(payload.get("next_after_id") or next_after_id or 0)
                    payload["reset"] = False
                    await self._send_to_connection(connection_id, payload)
                await asyncio.sleep(1.2)
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                watcher = self._log_watchers.get(connection_id)
                if watcher is asyncio.current_task():
                    self._log_watchers.pop(connection_id, None)

    async def shutdown(self) -> None:
        event_bus_task: asyncio.Task[None] | None = None
        async with self._lock:
            watchers = list(self._watchers.values())
            log_watchers = list(self._log_watchers.values())
            event_bus_task = self._event_bus_task
            self._event_bus_task = None
            self._event_bus_stop.set()
            self._watchers.clear()
            self._log_watchers.clear()
            self._scope_connections.clear()
            self._scope_subscriptions.clear()
            self._scope_visible_record_ids.clear()
            self._last_payloads.clear()
            self._connections.clear()
        for watcher in watchers + log_watchers + ([event_bus_task] if event_bus_task is not None else []):
            watcher.cancel()
        for watcher in watchers + log_watchers + ([event_bus_task] if event_bus_task is not None else []):
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


task_realtime_hub = TaskRealtimeHub()
