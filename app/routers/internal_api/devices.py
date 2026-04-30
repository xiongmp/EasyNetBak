from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session
from sqlmodel import select

from app.celery_app import celery_app
from app.celery_tasks import bulk_reachability_task
from app.db import get_session
from app.models import Device
from app.routers.support import _current_user, _log_action, _require_permission, get_user_allowed_group_ids
from app.routers.web_context import _dt_local_str
from app.services import backup_service, device_service
from celery.result import AsyncResult


router = APIRouter(tags=["设备管理 (Devices)"])


@router.post("/api/devices/{device_id}/backup", summary="手动触发备份(API)", description="通过API触发指定设备的配置备份")
def api_trigger_backup(request: Request, device_id: int, template_id: int = Form(0), session: Session = Depends(get_session)):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
    template_id = int(template_id) if template_id else 0
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    try:
        result = backup_service.trigger_backup(
            session,
            device_id=device_id,
            template_id=template_id,
            skip_email=False,
        allowed_group_ids=get_user_allowed_group_ids(_current_user(request), session=session),
        )
    except backup_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _log_action(request, session, "TRIGGER_BACKUP_API", "device", device_id, f"Backup Record ID: {result.record_id}")
    if not result.enqueued:
        raise HTTPException(status_code=503, detail="Celery 未启用或不可用")
    return {
        "record": {
            "id": str(result.record_id),
            "device_id": int(result.device_id),
            "started_at": _dt_local_str(result.started_at, offset_minutes=offset_minutes),
        }
    }


@router.post("/api/devices/bulk_backup", summary="批量备份(API)", description="通过API触发多个设备的配置备份")
def api_bulk_backup(
    request: Request,
    session: Session = Depends(get_session),
    device_ids: str = Form(""),
    mode: str = Form("selected"),
):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
    requested_ids = [int(value) for value in (device_ids or "").split(",") if value.strip().isdigit()]
    result = backup_service.trigger_bulk_backup(
        session,
        requested_ids=requested_ids,
        mode=mode,
        allowed_group_ids=get_user_allowed_group_ids(_current_user(request), session=session),
    )
    if not result.requested_ids:
        return {"records": []}
    if not result.jobs:
        return {"records": []}
    _log_action(
        request,
        session,
        "BULK_BACKUP_API",
        "device",
        None,
        f"Run ID: {result.run_id}, Jobs: {len(result.jobs)}",
    )
    if not result.enqueued:
        raise HTTPException(status_code=503, detail="Celery 未启用或不可用")
    return {"records": [str(record_id) for _, record_id, __ in result.jobs]}


@router.post("/api/devices/bulk_reachability", summary="批量测试连通性", description="异步批量测试多个设备的连通状态")
def api_bulk_reachability(
    request: Request,
    session: Session = Depends(get_session),
    device_ids: str = Form(""),
    q: str = Form(""),
    login_method: str = Form(""),
    platform: str = Form(""),
    group_id: int = Form(0),
    status: str = Form(""),
):
    _require_permission(request, "devices.update")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    ids = [int(value) for value in (device_ids or "").split(",") if value.strip().isdigit()]
    filters = device_service.normalize_list_filters(
        q=q,
        login_method=login_method,
        platform=platform,
        group_id=group_id,
        status=status,
    )
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)

    ids = device_service.resolve_reachability_device_ids(
        session,
        requested_ids=ids,
        raw_device_ids=device_ids,
        filters=filters,
        allowed_group_ids=allowed_group_ids,
    )

    if not ids:
        return {"task_id": None}

    task = bulk_reachability_task.delay(device_ids=ids, offset_minutes=offset_minutes)
    return {"task_id": task.id}


@router.get("/api/devices/reachability_tasks/{task_id}", summary="获取连通性测试状态", description="查询连通性测试任务进度")
def get_reachability_task_status(request: Request, task_id: str):
    _require_permission(request, "devices.update")
    result = AsyncResult(task_id, app=celery_app)
    if result.state == "PENDING":
        return {
            "id": task_id,
            "status": "pending",
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "items": [],
        }
    if result.state == "PROGRESS":
        meta = result.info or {}
        return {
            "id": task_id,
            "status": "running",
            "total": meta.get("total", 0),
            "processed": meta.get("processed", 0),
            "success": meta.get("success", 0),
            "failed": meta.get("failed", 0),
            "items": meta.get("items", []),
        }
    if result.state == "SUCCESS":
        payload = result.result or {}
        return {
            "id": task_id,
            "status": "finished",
            "total": payload.get("total", 0),
            "processed": payload.get("processed", 0),
            "success": payload.get("success", 0),
            "failed": payload.get("failed", 0),
            "items": payload.get("items", []),
        }
    if result.state == "FAILURE":
        return {
            "id": task_id,
            "status": "failed",
            "error": str(result.result),
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "items": [],
        }
    return {
        "id": task_id,
        "status": "running",
        "total": 0,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "items": [],
    }


@router.get("/api/devices/status", summary="获取设备状态统计", description="查询设备的健康状态与在线统计")
def get_devices_status(request: Request, ids: str = "", session: Session = Depends(get_session)):
    _require_permission(request, "devices.view")
    id_list = [int(value) for value in (ids or "").split(",") if value.strip().isdigit()]
    if not id_list:
        return {"items": []}

    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))

    devices = session.exec(select(Device).where(Device.id.in_(id_list))).all()
    results = []
    for device in devices:
        results.append(
            {
                "id": device.id,
                "success": device.reachability_status,
                "last_checked": _dt_local_str(device.last_reachability_check, offset_minutes=offset_minutes)
                if device.last_reachability_check
                else None,
                "error_message": device.reachability_error,
                "duration_ms": device.reachability_duration_ms,
            }
        )
    return {"items": results}
