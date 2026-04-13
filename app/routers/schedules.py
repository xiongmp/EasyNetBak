from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.core.time import parse_timezone_offset_to_minutes
from app.db import session_scope
from app.models import BackupRecord, BackupSchedule, BackupScheduleRunItem, Device
from app.routers.common import (
    _dt_local_str,
    _layout_context,
    _log_action,
    _require_permission,
    _require_any_permission,
    _current_user,
    get_user_allowed_group_ids,
    templates,
)
from app.scheduler import plan_schedule_run, resolve_device_ids_from_targets, sync_scheduler_from_db


router = APIRouter(tags=["定时任务 (Schedules)"])


@router.get("/schedules", summary="定时任务页面", description="查看自动化备份计划任务列表")
def schedules_page(request: Request):
    _require_permission(request, "schedules.view")
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        total = crud.count_schedules(session)
        items = crud.list_schedules(session, limit=limit, offset=offset)
        groups = crud.list_groups(session)
        devices = crud.list_devices(session)

        group_map = {g.id: g.name for g in groups if g.id}
        device_map = {d.id: d.name for d in devices if d.id}

        edit_id = request.query_params.get("edit")
        current = None
        if edit_id and edit_id.isdigit():
            current = crud.get_schedule(session, int(edit_id))
        last_runs = crud.list_latest_schedule_runs(session, [int(s.id) for s in items if s.id])
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/schedules?limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/schedules?limit={limit}&page="
         else:
             pagination_base = "/schedules?page="

    return templates.TemplateResponse(
        request=request,
        name="schedules.html",
        context={
            **_layout_context(request=request, active="schedules"),
            "items": items,
            "current": current,
            "groups": groups,
            "group_map": group_map,
            "device_map": device_map,
            "last_runs": last_runs,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )


@router.post("/schedules", summary="创建或更新定时任务", description="新增或修改定时备份任务")
def upsert_schedule(
    request: Request,
    schedule_id: int = Form(0),
    name: str = Form(""),
    enabled: str = Form("1"),
    crontab: str = Form("0 2 * * *"),
    targets: str = Form(""),
):
    if schedule_id and int(schedule_id) > 0:
        _require_permission(request, "schedules.update")
    else:
        _require_permission(request, "schedules.create")
    name = (name or "").strip()
    crontab = (crontab or "").strip()
    targets = targets or ""
    is_enabled = enabled in {"1", "true", "True", "yes", "YES", "on"}
    if not name:
        return RedirectResponse(url="/schedules?err=名称不能为空", status_code=303)
    try:
        CronTrigger.from_crontab(crontab)
    except Exception:
        return RedirectResponse(url="/schedules?err=Cron 表达式不合法", status_code=303)

    with session_scope() as session:
        # Check for duplicate name
        existing = session.exec(select(BackupSchedule).where(BackupSchedule.name == name)).first()
        if existing:
            if not schedule_id or int(schedule_id) <= 0:
                # Creating new schedule
                return RedirectResponse(url="/schedules?err=任务名称已存在", status_code=303)
            elif existing.id != int(schedule_id):
                # Updating existing schedule, but name conflicts with another one
                return RedirectResponse(url="/schedules?err=任务名称已存在", status_code=303)

        if schedule_id and int(schedule_id) > 0:
            updated = crud.update_schedule(
                session,
                int(schedule_id),
                name=name,
                crontab=crontab,
                enabled=is_enabled,
                targets=targets,
            )
            if updated is None:
                return RedirectResponse(url="/schedules?err=定时任务不存在", status_code=303)
            _log_action(request, session, "UPDATE_SCHEDULE", "schedule", schedule_id, f"Name: {name}, Enabled: {is_enabled}")
        else:
            sched = crud.create_schedule(
                session, schedule=BackupSchedule(name=name, crontab=crontab, enabled=is_enabled, targets=targets)
            )
            _log_action(request, session, "CREATE_SCHEDULE", "schedule", sched.id, f"Name: {name}, Enabled: {is_enabled}")

    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=已保存", status_code=303)


@router.post("/schedules/{schedule_id}/delete", summary="删除定时任务", description="删除指定的定时任务")
def delete_schedule(request: Request, schedule_id: int):
    _require_permission(request, "schedules.delete")
    with session_scope() as session:
        sched = crud.get_schedule(session, schedule_id)
        name = sched.name if sched else f"ID: {schedule_id}"
        crud.delete_schedule(session, int(schedule_id))
        _log_action(request, session, "DELETE_SCHEDULE", "schedule", schedule_id, f"Name: {name}")
    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=已删除", status_code=303)


@router.get("/schedules/{schedule_id}/stats", summary="任务统计", description="查看定时任务的执行统计数据")
def schedule_stats_page(request: Request, schedule_id: int):
    _require_permission(request, "schedules.view")
    with session_scope() as session:
        schedule = crud.get_schedule(session, int(schedule_id))
        runs = crud.list_schedule_runs(session, int(schedule_id), limit=120)
        run_ids = [r.id for r in runs if r.id]
        items = (
            list(session.exec(select(BackupScheduleRunItem).where(BackupScheduleRunItem.run_id.in_(run_ids))).all())
            if run_ids
            else []
        )
        backup_ids = [it.backup_id for it in items if it.backup_id]
        records = list(session.exec(select(BackupRecord).where(BackupRecord.id.in_(backup_ids))).all()) if backup_ids else []
        device_ids = sorted({int(it.device_id) for it in items if it.device_id})
        devices = list(session.exec(select(Device).where(Device.id.in_(device_ids))).all()) if device_ids else []
        groups = crud.list_groups(session)

    runs_sorted = sorted(runs, key=lambda r: r.started_at, reverse=False)
    finished_runs = [r for r in runs_sorted if r.finished_at and int(r.total_devices or 0) > 0]
    trend_runs = finished_runs[-30:]
    offset_minutes = int(getattr(getattr(request, "state", None), "tz_offset_minutes", parse_timezone_offset_to_minutes(settings.timezone_offset) or 0))
    trend = [
        {
            "started_at": _dt_local_str(r.started_at, offset_minutes=offset_minutes),
            "success": int(r.success_count or 0),
            "fail": int(r.fail_count or 0),
            "total": int(r.total_devices or 0),
            "rate": float(int(r.success_count or 0) / max(1, int(r.total_devices or 0))),
        }
        for r in trend_runs
    ]

    by_backup_id = {str(r.id): r for r in records}
    by_device_id = {int(d.id): d for d in devices if d.id}
    group_name_by_id = {int(g.id): g.name for g in groups if g.id}

    fail_by_device: dict[int, int] = {}
    total_by_group: dict[str, int] = {}
    fail_by_group: dict[str, int] = {}

    for it in items:
        rec = by_backup_id.get(str(it.backup_id))
        if rec is None or rec.finished_at is None:
            continue
        did = int(it.device_id)
        dev = by_device_id.get(did)
        gname = "未分组"
        if dev is not None:
            gid = int(getattr(dev, "group_id", 0) or 0)
            gname = group_name_by_id.get(gid, "未分组") if gid else "未分组"
        total_by_group[gname] = int(total_by_group.get(gname, 0) + 1)
        if not bool(rec.success):
            fail_by_device[did] = int(fail_by_device.get(did, 0) + 1)
            fail_by_group[gname] = int(fail_by_group.get(gname, 0) + 1)

    top_failed = sorted(
        [
            {
                "device_id": did,
                "fail_count": cnt,
                "name": (by_device_id.get(did).name if by_device_id.get(did) else f"device-{did}"),
                "host": (by_device_id.get(did).host if by_device_id.get(did) else ""),
                "platform": (by_device_id.get(did).platform if by_device_id.get(did) else ""),
            }
            for did, cnt in fail_by_device.items()
        ],
        key=lambda x: (-int(x["fail_count"]), x.get("host") or "", x.get("name") or ""),
    )[:10]

    group_summary = sorted(
        [
            {
                "group": gname,
                "total": int(total_by_group.get(gname, 0)),
                "fail": int(fail_by_group.get(gname, 0)),
            }
            for gname in total_by_group.keys()
        ],
        key=lambda x: (-int(x["total"]), x["group"]),
    )
    for row in group_summary:
        row["success"] = int(row["total"] - row["fail"])
        row["rate"] = float(int(row["success"]) / max(1, int(row["total"])))

    with session_scope() as session:
        all_devices = crud.list_devices(session)
        all_groups = crud.list_groups(session)
        device_map = {d.id: d.name for d in all_devices if d.id}
        group_map = {g.id: g.name for g in all_groups if g.id}

    return templates.TemplateResponse(
        request=request,
        name="schedule_stats.html",
        context={
            **_layout_context(request=request, active="schedules"),
            "schedule": schedule,
            "runs": runs,
            "trend": trend,
            "top_failed": top_failed,
            "group_summary": group_summary,
            "device_map": device_map,
            "group_map": group_map,
        },
    )


@router.post("/api/schedules/{schedule_id}/run", summary="手动触发任务", description="立即执行指定的定时任务")
def api_run_schedule(request: Request, schedule_id: int):
    _require_permission(request, "schedules.update")
    _require_permission(request, "backups.trigger")
    run_id, jobs = plan_schedule_run(schedule_id=int(schedule_id), trigger="manual")
    with session_scope() as session:
        _log_action(request, session, "TRIGGER_SCHEDULE_API", "schedule", schedule_id, f"Run ID: {run_id}, Jobs: {len(jobs)}")
    from app.celery_tasks import enqueue_schedule_run

    enqueued = enqueue_schedule_run(run_id=run_id, jobs=jobs)
    if not enqueued:
        with session_scope() as session:
            for _, backup_id, __ in jobs:
                record = crud.get_backup(session, backup_id)
                if record is None or record.finished_at is not None:
                    continue
                crud.finish_backup_record(
                    session,
                    record_id=backup_id,
                    success=False,
                    config_text=None,
                    error_message="Celery 未启用或不可用",
                    failure_type="ENQUEUE_FAILED",
                )
            run = crud.get_schedule_run(session, run_id)
            if run is not None and run.finished_at is None:
                crud.finish_schedule_run(
                    session,
                    run_id=run_id,
                    success_count=0,
                    fail_count=len(jobs),
                    error_message='{"enqueue_error":"CELERY_UNAVAILABLE"}',
                )
        raise HTTPException(status_code=503, detail="Celery 未启用或不可用")
    return {"run_id": str(run_id), "records": [str(rid) for _, rid, __ in jobs]}


@router.post("/api/schedules/{schedule_id}/toggle", summary="启停定时任务", description="启用或禁用指定的定时任务")
def api_toggle_schedule(request: Request, schedule_id: int):
    _require_permission(request, "schedules.update")
    with session_scope() as session:
        schedule = crud.get_schedule(session, schedule_id)
        if not schedule:
            return {"success": False, "message": "定时任务不存在"}
        
        new_status = not bool(schedule.enabled)
        crud.update_schedule(
            session,
            schedule_id,
            enabled=new_status
        )
        _log_action(request, session, "TOGGLE_SCHEDULE", "schedule", schedule_id, f"Enabled: {new_status}")
    
    sync_scheduler_from_db()
    return {"success": True, "enabled": new_status}


@router.get("/api/schedules/targets/groups", summary="获取任务目标分组", description="查询定时任务的候选设备分组")
def api_schedule_target_groups(request: Request):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    with session_scope() as session:
        groups = crud.list_groups(session)
        return [
            {
                "id": int(g.id),
                "name": g.name,
                "count": int(session.exec(select(func.count()).where(Device.group_id == g.id)).one()),
            }
            for g in groups
            if g.id
        ]


@router.get("/api/schedules/targets/platforms", summary="获取任务目标平台", description="查询定时任务的候选设备平台")
def api_schedule_target_platforms(request: Request):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    with session_scope() as session:
        platforms = list(session.exec(select(Device.platform).where(Device.platform.is_not(None)).distinct()).all())
        return [
            {"name": p, "count": int(session.exec(select(func.count()).where(Device.platform == p)).one())}
            for p in platforms
            if p and p.strip()
        ]


@router.get("/api/schedules/targets/devices", summary="获取任务目标设备", description="查询定时任务的候选设备")
def api_schedule_target_devices(
    request: Request,
    q: str = "",
    platform: str = "",
    group_id: int = 0,
    limit: int = 80,
):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    q = (q or "").strip() or None
    platform = (platform or "").strip() or None
    group_id_val = int(group_id) if int(group_id or 0) > 0 else None
    limit = max(1, min(200, int(limit or 80)))
    
    # Check permissions
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request))

    with session_scope() as session:
        total = crud.count_devices(
            session, q=q, platform=platform, group_id=group_id_val, allowed_group_ids=allowed_group_ids
        )
        devices = crud.search_devices(
            session, q=q, platform=platform, group_id=group_id_val, limit=limit, offset=0, allowed_group_ids=allowed_group_ids
        )
        groups = {int(g.id): g.name for g in crud.list_groups(session) if g.id}
    out = []
    for d in devices:
        gid = int(getattr(d, "group_id", 0) or 0)
        out.append(
            {
                "id": int(d.id or 0),
                "name": d.name,
                "host": d.host,
                "platform": d.platform,
                "group": (groups.get(gid, "未分组") if gid else "未分组"),
            }
        )
    return {"total": int(total), "devices": out}


@router.post("/api/schedules/preview", summary="预览任务目标", description="预览定时任务将会备份的设备列表")
def api_schedule_preview(request: Request, targets: str = Form("")):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    with session_scope() as session:
        ids = resolve_device_ids_from_targets(session, targets=targets)
        if not ids:
            return {"devices": [], "counts": {"total": 0, "platforms": {}, "groups": {}}}
        devices = list(session.exec(select(Device).where(Device.id.in_(ids))).all())
        groups = {int(g.id): g.name for g in crud.list_groups(session) if g.id}

    devices_sorted = sorted(devices, key=lambda d: (d.platform or "", d.host or "", d.id or 0))
    platforms: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    out_devices: list[dict[str, Any]] = []
    for d in devices_sorted:
        pid = (d.platform or "").strip()
        platforms[pid] = int(platforms.get(pid, 0) + 1)
        gid = int(getattr(d, "group_id", 0) or 0)
        gname = groups.get(gid, "未分组") if gid else "未分组"
        group_counts[gname] = int(group_counts.get(gname, 0) + 1)
        out_devices.append(
            {
                "id": int(d.id or 0),
                "name": d.name,
                "host": d.host,
                "platform": d.platform,
                "group": gname,
            }
        )

    return {"devices": out_devices, "counts": {"total": len(out_devices), "platforms": platforms, "groups": group_counts}}
