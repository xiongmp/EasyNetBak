from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func
from sqlmodel import Session
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.models import BackupRecord, BackupSchedule, BackupScheduleRunItem, Device
from app.core.time import format_local_datetime, normalize_timezone_offset, parse_timezone_offset_to_minutes
from app.scheduler import resolve_device_ids_from_targets
from app.services import pagination_service, task_state_service


from app.services.errors import ServiceError

logger = logging.getLogger(__name__)


_FAILURE_TYPE_LABELS = {
    "TIMEOUT": "超时",
    "READ_TIMEOUT": "读取超时",
    "DISCONNECTED": "连接中断",
    "SESSION_LIMIT": "会话数超限",
    "NETWORK_UNREACHABLE": "网络不可达",
    "REFUSED": "连接被拒绝",
    "TASK_FAILURE": "任务异常",
    "TASK_REVOKED": "任务已撤销",
    "TIME_LIMIT": "执行超时",
    "ENQUEUE_FAILED": "入队失败",
    "DEVICE_NOT_FOUND": "设备不存在",
    "TEMPLATE_NOT_FOUND": "模板不存在",
    "PLATFORM_MISMATCH": "模板与平台不匹配",
    "CANCELLED": "已终止",
    "UNKNOWN": "未知异常",
}


def _failure_type_label(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    return _FAILURE_TYPE_LABELS.get(normalized, normalized or "未知异常")


def _format_failure_summary(failures_by_type: dict[str, Any]) -> str:
    rows: list[str] = []
    for failure_type, count in failures_by_type.items():
        amount = max(0, int(count or 0))
        if amount <= 0:
            continue
        rows.append(f"{_failure_type_label(str(failure_type))} {amount} 个")
    return "，".join(rows)


def summarize_schedule_run_error(error_message: str | None) -> str:
    raw = str(error_message or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw

    if not isinstance(payload, dict):
        return raw

    parts: list[str] = []
    termination_mode = str(payload.get("termination_mode") or "").strip()
    cancelled_backups = max(0, int(payload.get("cancelled_backups") or 0))
    unfinished_backups = max(0, int(payload.get("unfinished_backups") or 0))
    failures_by_type = payload.get("failures_by_type")

    if termination_mode == "pending_only" and cancelled_backups > 0:
        parts.append(f"已终止 {cancelled_backups} 个未运行任务")
    elif cancelled_backups > 0:
        parts.append(f"已终止 {cancelled_backups} 个任务")

    if unfinished_backups > 0:
        if termination_mode == "pending_only":
            parts.append(f"仍有 {unfinished_backups} 个任务执行中")
        else:
            parts.append(f"仍有 {unfinished_backups} 个任务未结束")

    if isinstance(failures_by_type, dict):
        failure_summary = _format_failure_summary(failures_by_type)
        if failure_summary:
            parts.append(f"失败类型：{failure_summary}")

    if parts:
        return "；".join(parts)
    return raw


def _serialize_schedule_run(run: BackupScheduleRun, *, offset_minutes: int = 0) -> dict[str, Any]:
    started_at = getattr(run, "started_at", None)
    finished_at = getattr(run, "finished_at", None)
    status = getattr(run, "status", None)
    started_at_text = format_local_datetime(started_at, offset_minutes=offset_minutes) if started_at else ""
    finished_at_text = format_local_datetime(finished_at, offset_minutes=offset_minutes) if finished_at else None

    duration_seconds: int | None = None
    duration_text = "—"
    if started_at and finished_at:
        duration_seconds = max(0, int((finished_at - started_at).total_seconds()))
        if duration_seconds < 60:
            duration_text = f"{duration_seconds}s"
        else:
            duration_text = f"{duration_seconds // 60}m {duration_seconds % 60}s"
    elif task_state_service.is_schedule_run_active_status(status):
        duration_text = task_state_service.get_schedule_run_status_label(status)

    return {
        "id": str(run.id),
        "trigger": str(getattr(run, "trigger", "") or ""),
        "started_at": started_at_text,
        "finished_at": finished_at_text,
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
        "total_devices": int(getattr(run, "total_devices", 0) or 0),
        "success_count": int(getattr(run, "success_count", 0) or 0),
        "fail_count": int(getattr(run, "fail_count", 0) or 0),
        "status": str(status or ""),
        "status_label": task_state_service.get_schedule_run_status_label(status),
        "status_tone": task_state_service.get_schedule_run_status_tone(status),
        "error_message": str(getattr(run, "error_message", "") or ""),
        "error_summary": summarize_schedule_run_error(getattr(run, "error_message", None)),
    }


def upsert_schedule(
    session: Session,
    *,
    schedule_id: int = 0,
    name: str,
    crontab: str,
    enabled: bool,
    targets: str,
) -> BackupSchedule:
    name = (name or "").strip()
    crontab = (crontab or "").strip()
    targets = normalize_schedule_targets(session, targets=targets)
    if not name:
        raise ServiceError("名称不能为空", code="SCHEDULE_NAME_REQUIRED")
    try:
        CronTrigger.from_crontab(crontab)
    except Exception as exc:
        raise ServiceError("Cron 表达式不合法", code="SCHEDULE_CRONTAB_INVALID") from exc

    existing = session.exec(select(BackupSchedule).where(BackupSchedule.name == name)).first()
    if existing:
        if not schedule_id or int(schedule_id) <= 0:
            raise ServiceError("任务名称已存在", code="SCHEDULE_NAME_CONFLICT")
        if existing.id != int(schedule_id):
            raise ServiceError("任务名称已存在", code="SCHEDULE_NAME_CONFLICT")

    if schedule_id and int(schedule_id) > 0:
        updated = crud.update_schedule(
            session,
            int(schedule_id),
            name=name,
            crontab=crontab,
            enabled=enabled,
            targets=targets,
        )
        if updated is None:
            raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)
        return updated

    return crud.create_schedule(
        session,
        schedule=BackupSchedule(name=name, crontab=crontab, enabled=enabled, targets=targets),
    )


def normalize_schedule_targets(session: Session, *, targets: str) -> str:
    tokens = [token.strip() for token in (targets or "").splitlines() if token.strip()]
    if not tokens:
        return ""  # 允许为空，表示不匹配任何设备

    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lower = token.lower()
        if lower == "all":
            return "all"
        if token.startswith("group:"):
            value = token.split(":", 1)[1].strip()
            group = None
            if value.isdigit():
                group = crud.get_group(session, int(value))
            else:
                group = crud.get_group_by_name(session, value)
            if group and group.id:
                normalized_token = f"group:{int(group.id)}"
            else:
                normalized_token = token
        else:
            normalized_token = token
        if normalized_token not in seen:
            seen.add(normalized_token)
            normalized.append(normalized_token)
    return "\n".join(normalized) if normalized else "all"


def list_legacy_group_name_targets(session: Session) -> list[dict[str, Any]]:
    legacy_items: list[dict[str, Any]] = []
    for schedule in crud.list_schedules(session):
        tokens = [token.strip() for token in (schedule.targets or "").splitlines() if token.strip()]
        legacy_targets = []
        for token in tokens:
            if not token.startswith("group:"):
                continue
            value = token.split(":", 1)[1].strip()
            if not value or value.isdigit():
                continue
            group = crud.get_group_by_name(session, value)
            if group and group.id:
                legacy_targets.append(
                    {
                        "raw": token,
                        "normalized": f"group:{int(group.id)}",
                    }
                )
        if legacy_targets:
            legacy_items.append(
                {
                    "schedule_id": int(schedule.id or 0),
                    "schedule_name": schedule.name,
                    "targets": legacy_targets,
                }
            )
    return legacy_items


def delete_schedule(session: Session, schedule_id: int) -> str:
    schedule = crud.get_schedule(session, schedule_id)
    if schedule is None:
        raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)
    if schedule and crud.has_active_runs_for_schedule(session, int(schedule_id)):
        raise ServiceError(
            "Schedule has active runs",
            code="SCHEDULE_DELETE_ACTIVE_RUNS",
            status_code=409,
            context={"schedule_id": int(schedule_id)},
        )
    name = schedule.name if schedule else f"ID: {schedule_id}"
    crud.delete_schedule(session, int(schedule_id))
    return name


def toggle_schedule(session: Session, schedule_id: int) -> bool:
    schedule = crud.get_schedule(session, schedule_id)
    if not schedule:
        raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)

    new_status = not bool(schedule.enabled)
    crud.update_schedule(session, schedule_id, enabled=new_status)
    return new_status


def _build_next_run_payload(
    schedules: list[BackupSchedule],
    *,
    timezone_offset: str | None,
) -> dict[int, dict[str, str]]:
    tz_value = normalize_timezone_offset(timezone_offset, default=settings.timezone_offset)
    offset_minutes = parse_timezone_offset_to_minutes(tz_value) or 0
    tzinfo = timezone(timedelta(minutes=offset_minutes))
    now = datetime.now(tzinfo)
    next_runs: dict[int, dict[str, str]] = {}

    for schedule in schedules:
        if not schedule.id:
            continue
        schedule_id = int(schedule.id)
        if not bool(schedule.enabled):
            next_runs[schedule_id] = {"text": "已禁用", "tone": "secondary"}
            continue

        crontab = (schedule.crontab or "").strip()
        if not crontab:
            next_runs[schedule_id] = {"text": "未配置", "tone": "secondary"}
            continue

        try:
            trigger = CronTrigger.from_crontab(crontab, timezone=tzinfo)
            next_fire_time = trigger.get_next_fire_time(None, now)
        except Exception:
            next_runs[schedule_id] = {"text": "Cron 无效", "tone": "danger"}
            continue

        if next_fire_time is None:
            next_runs[schedule_id] = {"text": "无后续执行", "tone": "secondary"}
            continue

        next_runs[schedule_id] = {
            "text": format_local_datetime(next_fire_time, offset_minutes=offset_minutes),
            "tone": "primary",
        }

    return next_runs


def get_schedule_next_run_payload(session: Session, *, schedule_id: int) -> dict[str, str]:
    schedule = crud.get_schedule(session, int(schedule_id))
    if schedule is None:
        raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)
    timezone_offset = crud.get_setting(session, key="timezone_offset")
    return _build_next_run_payload([schedule], timezone_offset=timezone_offset).get(
        int(schedule_id),
        {"text": "未知", "tone": "secondary"},
    )


def get_schedule_page_payload(
    session: Session,
    *,
    page: int = 1,
    limit: int = 10,
    edit_id: str | None = None,
    include_limit_param: bool = False,
) -> dict[str, Any]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=include_limit_param,
        default_limit=10,
        max_limit=100,
    )

    total = crud.count_schedules(session)
    items = crud.list_schedules(session, limit=params.limit, offset=params.offset)
    groups = crud.list_groups(session)
    devices = crud.list_devices(session)

    current = None
    if edit_id and str(edit_id).isdigit():
        current = crud.get_schedule(session, int(edit_id))

    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/schedules",
        params={},
        limit=pagination.limit,
        default_limit=10,
        limit_explicit=params.limit_explicit,
    )

    def _get_full_path(gid: int) -> str:
        parts = []
        curr = crud.get_group(session, gid)
        while curr:
            parts.insert(0, curr.name)
            if curr.parent_id:
                curr = crud.get_group(session, curr.parent_id)
            else:
                break
        return "/".join(parts) if parts else "未分组"

    group_map = {g.id: _get_full_path(int(g.id)) for g in groups if g.id}
    timezone_offset = crud.get_setting(session, key="timezone_offset")

    return {
        "items": items,
        "current": current,
        "groups": groups,
        "group_map": group_map,
        "device_map": {d.id: d.name for d in devices if d.id},
        "last_runs": crud.list_latest_schedule_runs(session, [int(schedule.id) for schedule in items if schedule.id]),
        "next_runs": _build_next_run_payload(items, timezone_offset=timezone_offset),
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
    }


def get_schedule_stats_payload(
    session: Session,
    *,
    schedule_id: int,
    offset_minutes: int = 0,
) -> dict[str, Any]:
    schedule = crud.get_schedule(session, int(schedule_id))
    if schedule is None:
        raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)
    runs = crud.list_schedule_runs(session, int(schedule_id), limit=120)
    run_ids = [run.id for run in runs if run.id]
    items = (
        list(session.exec(select(BackupScheduleRunItem).where(BackupScheduleRunItem.run_id.in_(run_ids))).all()) if run_ids else []
    )
    backup_ids = [item.backup_id for item in items if item.backup_id]
    records = list(session.exec(select(BackupRecord).where(BackupRecord.id.in_(backup_ids))).all()) if backup_ids else []
    device_ids = sorted({int(item.device_id) for item in items if item.device_id})
    devices = list(session.exec(select(Device).where(Device.id.in_(device_ids))).all()) if device_ids else []
    groups = crud.list_groups(session)
    all_devices = crud.list_devices(session)
    all_groups = groups

    runs_sorted = sorted(runs, key=lambda run: run.started_at, reverse=False)
    finished_runs = [
        run
        for run in runs_sorted
        if task_state_service.is_schedule_run_terminal_status(run.status) and int(run.total_devices or 0) > 0
    ]
    trend_runs = finished_runs[-30:]
    trend = [
        {
            "started_at": format_local_datetime(run.started_at, offset_minutes=offset_minutes),
            "success": int(run.success_count or 0),
            "fail": int(run.fail_count or 0),
            "total": int(run.total_devices or 0),
            "rate": float(int(run.success_count or 0) / max(1, int(run.total_devices or 0))),
        }
        for run in trend_runs
    ]

    by_backup_id = {str(record.id): record for record in records}
    by_device_id = {int(device.id): device for device in devices if device.id}
    
    # 建立全路径映射
    def _get_full_path_local(group_id: int) -> str:
        path = []
        curr_id = group_id
        visited = set()
        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            g = next((x for x in all_groups if int(x.id) == curr_id), None)
            if not g:
                break
            path.append(g.name)
            curr_id = int(g.parent_id) if g.parent_id else 0
        return "/".join(reversed(path)) if path else "未分组"

    full_path_by_id = {int(group.id): _get_full_path_local(int(group.id)) for group in groups if group.id}

    fail_by_device: dict[int, int] = {}
    total_by_group: dict[str, int] = {}
    fail_by_group: dict[str, int] = {}

    for item in items:
        record = by_backup_id.get(str(item.backup_id))
        if record is None or not task_state_service.is_backup_record_terminal_status(record.status):
            continue
        device_id = int(item.device_id)
        device = by_device_id.get(device_id)
        group_name = "未分组"
        if device is not None:
            group_id = int(getattr(device, "group_id", 0) or 0)
            group_name = full_path_by_id.get(group_id, "未分组") if group_id else "未分组"
        total_by_group[group_name] = int(total_by_group.get(group_name, 0) + 1)
        if not bool(record.success):
            fail_by_device[device_id] = int(fail_by_device.get(device_id, 0) + 1)
            fail_by_group[group_name] = int(fail_by_group.get(group_name, 0) + 1)

    top_failed = sorted(
        [
            {
                "device_id": device_id,
                "fail_count": count,
                "name": (by_device_id.get(device_id).name if by_device_id.get(device_id) else f"device-{device_id}"),
                "host": (by_device_id.get(device_id).host if by_device_id.get(device_id) else ""),
                "platform": (by_device_id.get(device_id).platform if by_device_id.get(device_id) else ""),
            }
            for device_id, count in fail_by_device.items()
        ],
        key=lambda row: (-int(row["fail_count"]), row.get("host") or "", row.get("name") or ""),
    )[:10]

    group_summary = sorted(
        [
            {
                "group": group_name,
                "total": int(total_by_group.get(group_name, 0)),
                "fail": int(fail_by_group.get(group_name, 0)),
            }
            for group_name in total_by_group.keys()
        ],
        key=lambda row: (-int(row["total"]), row["group"]),
    )
    for row in group_summary:
        row["success"] = int(row["total"] - row["fail"])
        row["rate"] = float(int(row["success"]) / max(1, int(row["total"])))

    return {
        "schedule": schedule,
        "runs": runs,
        "trend": trend,
        "top_failed": top_failed,
        "group_summary": group_summary,
        "run_status_labels": {
            str(run.id): task_state_service.get_schedule_run_status_label(run.status)
            for run in runs
            if getattr(run, "id", None)
        },
        "run_status_tones": {
            str(run.id): task_state_service.get_schedule_run_status_tone(run.status)
            for run in runs
            if getattr(run, "id", None)
        },
        "run_error_summaries": {
            str(run.id): summarize_schedule_run_error(getattr(run, "error_message", None))
            for run in runs
            if getattr(run, "id", None)
        },
        "device_map": {device.id: device.name for device in all_devices if device.id},
        "group_map": full_path_by_id,
    }


def get_schedule_runs_live_payload(
    session: Session,
    *,
    schedule_id: int,
    offset_minutes: int = 0,
    limit: int = 30,
) -> dict[str, Any]:
    schedule = crud.get_schedule(session, int(schedule_id))
    if schedule is None:
        raise ServiceError("定时任务不存在", code="SCHEDULE_NOT_FOUND", status_code=404)
    runs = crud.list_schedule_runs(session, int(schedule_id), limit=max(1, int(limit or 30)))
    items = [_serialize_schedule_run(run, offset_minutes=offset_minutes) for run in runs]
    has_active_runs = any(task_state_service.is_schedule_run_active_status(item.get("status")) for item in items)
    return {
        "schedule_id": int(schedule_id),
        "items": items,
        "has_active_runs": has_active_runs,
    }


def _get_full_path(session: Session, gid: int) -> str:
    parts = []
    curr = crud.get_group(session, gid)
    while curr:
        parts.insert(0, curr.name)
        if curr.parent_id:
            curr = crud.get_group(session, curr.parent_id)
        else:
            break
    return "/".join(parts) if parts else "未分组"

def list_schedule_target_groups(
    session: Session,
    *,
    allowed_group_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    from app.services import resource_service
    tree = resource_service.list_group_tree(session)
    flat_groups = []
    def _flatten(nodes):
        for node in nodes:
            if allowed_group_ids is None or node["id"] in allowed_group_ids:
                flat_groups.append(node)
            if node.get("children"):
                _flatten(node["children"])
    _flatten(tree)

    return [
        {
            "id": int(group["id"]),
            "name": group["name"],
            "parent_id": group["parent_id"],
            "path": group["path"],
            "depth": group["depth"],
            "count": crud.group_subtree_usage_count(session, int(group["id"])),
            "full_path": _get_full_path(session, int(group["id"])),
        }
        for group in flat_groups
    ]


def list_schedule_target_platforms(session: Session) -> list[dict[str, Any]]:
    platforms = list(session.exec(select(Device.platform).where(Device.platform.is_not(None)).distinct()).all())
    return [
        {"name": platform, "count": int(session.exec(select(func.count()).where(Device.platform == platform)).one())}
        for platform in platforms
        if platform and platform.strip()
    ]


def list_schedule_target_devices(
    session: Session,
    *,
    q: str | None = None,
    platform: str | None = None,
    group_id: int | None = None,
    limit: int = 80,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    limit = max(1, min(200, int(limit or 80)))
    total = crud.count_devices(
        session,
        q=q,
        platform=platform,
        group_id=group_id,
        allowed_group_ids=allowed_group_ids,
    )
    devices = crud.search_devices(
        session,
        q=q,
        platform=platform,
        group_id=group_id,
        limit=limit,
        offset=0,
        allowed_group_ids=allowed_group_ids,
    )
    groups = {int(group.id): group.name for group in crud.list_groups(session) if group.id}
    return {
        "total": int(total),
        "devices": [
            {
                "id": int(device.id or 0),
                "name": device.name,
                "host": device.host,
                "platform": device.platform,
                "group": _get_full_path(session, int(getattr(device, "group_id", 0) or 0)) if getattr(device, "group_id", 0) else "未分组",
            }
            for device in devices
        ],
    }


def preview_schedule_targets(
    session: Session,
    *,
    targets: str = "",
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    device_ids = resolve_device_ids_from_targets(session, targets=targets)
    if not device_ids:
        return {"devices": [], "counts": {"total": 0, "platforms": {}, "groups": {}}}

    devices = list(session.exec(select(Device).where(Device.id.in_(device_ids))).all())
    if allowed_group_ids is not None:
        allowed_set = set(allowed_group_ids)
        devices = [
            device
            for device in devices
            if int(getattr(device, "group_id", 0) or 0) in allowed_set
            or (
                int(getattr(device, "group_id", 0) or 0) == 0
                and (-1 in allowed_set or 0 in allowed_set)
            )
        ]
    groups = {int(group.id): group.name for group in crud.list_groups(session) if group.id}
    devices_sorted = sorted(devices, key=lambda device: (device.platform or "", device.host or "", device.id or 0))

    platforms: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    out_devices: list[dict[str, Any]] = []
    for device in devices_sorted:
        platform = (device.platform or "").strip()
        platforms[platform] = int(platforms.get(platform, 0) + 1)
        group_id = int(getattr(device, "group_id", 0) or 0)
        group_name = "未分组"
        if group_id and group_id in groups:
            group_name = _get_full_path(session, group_id)
            
        group_counts[group_name] = int(group_counts.get(group_name, 0) + 1)
        out_devices.append(
            {
                "id": int(device.id or 0),
                "name": device.name,
                "host": device.host,
                "platform": device.platform,
                "group": group_name,
            }
        )

    return {"devices": out_devices, "counts": {"total": len(out_devices), "platforms": platforms, "groups": group_counts}}
