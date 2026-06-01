from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.db import get_session
from app.routers.support import _current_user, _log_action, _require_permission, get_user_allowed_group_ids
from app.services import backup_service


router = APIRouter(tags=["备份管理 (Backups)"])


@router.get("/api/tasks/backups", summary="获取备份任务", description="查询正在执行或历史备份任务")
def api_tasks_backups(request: Request, ids: str = "", limit: int = 20, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    limit = max(1, min(int(limit or 20), 200))
    wanted: list[UUID] = []
    for raw in (ids or "").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            wanted.append(UUID(value))
        except Exception:
            continue

    return backup_service.list_task_backups(
        session,
        wanted_ids=wanted,
        limit=limit,
        offset_minutes=offset_minutes,
        allowed_group_ids=allowed_group_ids,
    )


@router.get("/api/tasks/celery", summary="获取Celery任务", description="查询异步后台任务状态")
def api_tasks_celery(request: Request, ids: str = ""):
    _require_permission(request, "backups.view")

    task_ids = [value.strip() for value in (ids or "").split(",") if value.strip()]
    if not task_ids:
        return {"enabled": False, "items": []}

    try:
        from celery.result import AsyncResult

        from app.celery_app import celery_app
        from app.celery_tasks import celery_enabled

        if not celery_enabled():
            return {"enabled": False, "items": [{"id": task_id, "state": "DISABLED"} for task_id in task_ids]}

        items: list[dict[str, object]] = []
        for task_id in task_ids:
            result = AsyncResult(str(task_id), app=celery_app)
            items.append(
                {
                    "id": str(task_id),
                    "state": str(result.state),
                    "ready": bool(result.ready()),
                    "successful": bool(result.successful()) if result.ready() else False,
                    "failed": bool(result.failed()) if result.ready() else False,
                }
            )
        return {"enabled": True, "items": items}
    except Exception:
        return {"enabled": False, "items": [{"id": task_id, "state": "UNKNOWN"} for task_id in task_ids]}


@router.get("/api/devices/{device_id}/backups", summary="获取设备备份记录", description="查询指定设备的历史配置备份")
def api_device_backups(request: Request, device_id: int, page: int = 1, limit: int = 10, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        return backup_service.list_device_backups_payload(
            session,
            device_id=device_id,
            page=page,
            limit=limit,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
    except backup_service.ServiceError as exc:
        if exc.code == "BACKUP_DEVICE_NOT_FOUND":
            raise HTTPException(status_code=404, detail="设备不存在")
        if exc.code in {"BACKUP_DEVICE_FORBIDDEN", "DEVICE_ACCESS_FORBIDDEN"} or int(getattr(exc, "status_code", 400)) == 403:
            raise HTTPException(status_code=403, detail="无权访问该设备")
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/api/backups/{backup_id}", summary="获取备份详情", description="查询指定备份记录的详细内容")
def api_backup_view(request: Request, backup_id: UUID, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        return backup_service.get_backup_view_payload(
            session,
            backup_id,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
    except backup_service.ServiceError as exc:
        if exc.code == "BACKUP_NOT_FOUND":
            raise HTTPException(status_code=404, detail="备份记录不存在")
        if exc.code in {"BACKUP_DEVICE_FORBIDDEN", "DEVICE_ACCESS_FORBIDDEN"} or int(getattr(exc, "status_code", 400)) == 403:
            raise HTTPException(status_code=403, detail="无权访问该备份记录")
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/api/backups/{backup_id}/diff/{other_id}", summary="对比备份差异", description="对比两次设备配置备份的差异")
def api_backup_diff(
    request: Request,
    backup_id: UUID,
    other_id: UUID,
    session: Session = Depends(get_session),
    mode: str = "unified",
    only_changed_lines: int = 1,
    ignore_noise_lines: int = 0,
    context_lines: int = 2,
):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    only_changed = bool(int(only_changed_lines or 0))
    ignore_noise = bool(int(ignore_noise_lines or 0))
    try:
        return backup_service.build_backup_diff(
            session,
            backup_id=backup_id,
            other_id=other_id,
            mode=mode,
            only_changed_lines=only_changed,
            ignore_noise_lines=ignore_noise,
            context_lines=context_lines,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
    except backup_service.ServiceError as exc:
        if exc.code == "BACKUP_NOT_FOUND":
            raise HTTPException(status_code=404, detail="备份记录不存在")
        if exc.code in {"BACKUP_DEVICE_FORBIDDEN", "DEVICE_ACCESS_FORBIDDEN"} or int(getattr(exc, "status_code", 400)) == 403:
            raise HTTPException(status_code=403, detail="无权访问该备份记录")
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/api/backups/{backup_id}/delete", summary="删除备份记录", description="删除指定的设备备份记录")
def api_delete_backup(request: Request, backup_id: UUID, session: Session = Depends(get_session)):
    _require_permission(request, "backups.delete")
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        detail = backup_service.delete_backup(session, backup_id, allowed_group_ids=allowed_group_ids)
    except backup_service.ServiceError as exc:
        if exc.code == "BACKUP_NOT_FOUND":
            raise HTTPException(status_code=404, detail="备份记录不存在")
        if exc.code == "BACKUP_DELETE_ACTIVE_RECORD":
            raise HTTPException(status_code=409, detail="执行中的备份任务无法删除")
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    record = detail.record
    device = detail.device
    device_name = device.name if device and device.name else f"device-{record.device_id}"
    _log_action(request, session, "DELETE_BACKUP", "backup", str(backup_id), f"Device: {device_name}")
    return {"success": True, "deleted": 1, "id": str(backup_id)}
