from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app import crud
from app.db import engine, get_session
from app.core.settings import settings
from app.routers.support import _current_user, _log_action, _require_permission, get_remote_ip, get_user_allowed_group_ids, has_permission
from app.services import backup_service, request_context_service, task_event_bus_service, task_orchestration_service, task_realtime_service
from app.services.auth import decode_session_token


router = APIRouter(tags=["备份管理 (Backups)"])


class BackupBatchQueryRequest(BaseModel):
    run_id: UUID | None = None
    backup_id: UUID | None = None


def _parse_selected_backup_ids(raw_value: object) -> list[UUID]:
    if not isinstance(raw_value, list):
        return []
    selected: list[UUID] = []
    seen: set[UUID] = set()
    for raw in raw_value:
        try:
            backup_id = UUID(str(raw))
        except Exception:
            continue
        if backup_id in seen:
            continue
        seen.add(backup_id)
        selected.append(backup_id)
    return selected


def _load_task_ws_context(user_id: int) -> dict[str, object] | None:
    context = request_context_service.load_auth_context(user_id=user_id)
    user = context.user
    if user is None:
        return None
    with Session(engine, expire_on_commit=False) as session:
        allowed_group_ids = get_user_allowed_group_ids(user, session=session)
    return {
        "user": user,
        "allowed_group_ids": allowed_group_ids,
        "tz_offset_minutes": int(context.tz_offset_minutes or 0),
    }


def _create_websocket_audit_log(
    *,
    user,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: str | None,
    ip_address: str | None,
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        crud.create_audit_log(
            session,
            user_id=int(user.id) if user and user.id else None,
            username=user.username if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
        )
        session.commit()


async def _handle_task_ws_message(
    *,
    websocket: WebSocket,
    connection_id: str,
    payload: dict[str, object],
    user,
    allowed_group_ids: list[int] | None,
    tz_offset_minutes: int,
    locale: str = "zh-CN",
) -> None:
    action = str(payload.get("action") or "").strip().lower()
    if action == "ping":
        await task_realtime_service.task_realtime_hub.send(connection_id, {"type": "pong"})
        return

    if action == "unsubscribe":
        await task_realtime_service.task_realtime_hub.clear_subscription(connection_id)
        await task_realtime_service.task_realtime_hub.send(connection_id, {"type": "task_unsubscribed"})
        return

    if action == "unsubscribe_logs":
        await task_realtime_service.task_realtime_hub.clear_log_subscription(connection_id)
        await task_realtime_service.task_realtime_hub.send(connection_id, {"type": "task_logs_unsubscribed"})
        return

    if action == "subscribe":
        try:
            subscription = task_realtime_service.build_subscription(
                run_id=payload.get("run_id"),
                backup_id=payload.get("backup_id"),
                allowed_group_ids=allowed_group_ids,
                tz_offset_minutes=tz_offset_minutes,
                locale=locale,
            )
        except Exception as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_error", "message": f"订阅参数无效: {str(exc)}"},
            )
            return
        await task_realtime_service.task_realtime_hub.subscribe(connection_id, subscription)
        return

    if action == "subscribe_logs":
        try:
            subscription = task_realtime_service.build_subscription(
                run_id=payload.get("run_id"),
                backup_id=payload.get("backup_id"),
                allowed_group_ids=allowed_group_ids,
                tz_offset_minutes=tz_offset_minutes,
                locale=locale,
            )
        except Exception as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_error", "message": f"日志订阅参数无效: {str(exc)}"},
            )
            return
        after_id_raw = payload.get("after_id")
        try:
            after_id = int(after_id_raw) if after_id_raw not in (None, "") else None
        except Exception:
            after_id = None
        await task_realtime_service.task_realtime_hub.subscribe_logs(
            connection_id,
            subscription,
            after_id=after_id,
        )
        return

    if action == "terminate_run":
        if not has_permission(user, "schedules.update"):
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "缺少 schedules.update 权限"},
            )
            return
        run_id_raw = payload.get("run_id")
        try:
            run_id = UUID(str(run_id_raw))
        except Exception:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "run_id 无效"},
            )
            return

        def _terminate() -> dict[str, object]:
            with Session(engine, expire_on_commit=False) as session:
                result = task_orchestration_service.terminate_schedule_run(session, run_id=run_id)
                session.commit()
            _create_websocket_audit_log(
                user=user,
                action="TERMINATE_SCHEDULE_RUN_PENDING",
                resource_type="schedule_run",
                resource_id=str(run_id),
                details=(
                    f"Schedule ID: {result.schedule_id}, Status: {result.status}, "
                    f"Terminated: {result.terminated_records}, Skipped: {result.skipped_records}, "
                    f"Running: {result.running_records}"
                ),
                ip_address=get_remote_ip(websocket),
            )
            return {
                "run_id": str(result.run_id),
                "schedule_id": int(result.schedule_id),
                "status": result.status,
                "terminated_records": int(result.terminated_records),
                "skipped_records": int(result.skipped_records),
                "running_records": int(result.running_records),
                "message": result.message,
            }

        try:
            response = await run_in_threadpool(_terminate)
        except task_orchestration_service.ServiceError as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": exc.message},
            )
            return
        task_event_bus_service.publish_task_state_hint(run_id=str(run_id), event="terminate_schedule_run")
        await task_realtime_service.task_realtime_hub.send(
            connection_id,
            {"type": "task_command_result", "action": action, "ok": True, **response},
        )
        return

    if action == "terminate_selected":
        if not has_permission(user, "schedules.update"):
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "缺少 schedules.update 权限"},
            )
            return
        run_id_raw = payload.get("run_id")
        try:
            run_id = UUID(str(run_id_raw))
        except Exception:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "run_id 无效"},
            )
            return
        selected_backup_ids = _parse_selected_backup_ids(payload.get("backup_ids"))
        if not selected_backup_ids:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "请选择至少一个任务"},
            )
            return

        def _terminate_selected() -> dict[str, object]:
            with Session(engine, expire_on_commit=False) as session:
                result = task_orchestration_service.terminate_selected_schedule_run(
                    session,
                    run_id=run_id,
                    backup_ids=selected_backup_ids,
                    allowed_group_ids=allowed_group_ids,
                )
                session.commit()
            _create_websocket_audit_log(
                user=user,
                action="TERMINATE_SCHEDULE_RUN_SELECTED",
                resource_type="schedule_run",
                resource_id=str(run_id),
                details=(
                    f"Schedule ID: {result.schedule_id}, Status: {result.status}, "
                    f"Selected: {result.selected_records}, Terminated: {result.terminated_records}, "
                    f"Skipped: {result.skipped_records}, Running: {result.running_records}"
                ),
                ip_address=get_remote_ip(websocket),
            )
            return {
                "run_id": str(result.run_id),
                "schedule_id": int(result.schedule_id),
                "status": result.status,
                "selected_records": int(result.selected_records),
                "terminated_records": int(result.terminated_records),
                "skipped_records": int(result.skipped_records),
                "running_records": int(result.running_records),
                "message": result.message,
            }

        try:
            response = await run_in_threadpool(_terminate_selected)
        except task_orchestration_service.ServiceError as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": exc.message},
            )
            return
        task_event_bus_service.publish_task_state_hint(
            run_id=str(run_id),
            event="terminate_selected_schedule_run",
            details={"record_ids": [str(backup_id) for backup_id in selected_backup_ids]},
        )
        await task_realtime_service.task_realtime_hub.send(
            connection_id,
            {"type": "task_command_result", "action": action, "ok": True, **response},
        )
        return

    if action == "retry_run":
        if not has_permission(user, "devices.backup"):
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "缺少 devices.backup 权限"},
            )
            return
        run_id_raw = payload.get("run_id")
        try:
            run_id = UUID(str(run_id_raw))
        except Exception:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "run_id 无效"},
            )
            return

        def _retry() -> dict[str, object]:
            with Session(engine, expire_on_commit=False) as session:
                result = task_orchestration_service.retry_schedule_run(
                    session,
                    run_id=run_id,
                    allowed_group_ids=allowed_group_ids,
                )
                session.commit()
            _create_websocket_audit_log(
                user=user,
                action="RETRY_SCHEDULE_RUN",
                resource_type="schedule_run",
                resource_id=str(run_id),
                details=(
                    f"Source Run ID: {result.source_run_id}, New Run ID: {result.new_run_id}, "
                    f"Schedule ID: {result.schedule_id}, Retried: {result.retried_records}, "
                    f"Skipped: {result.skipped_records}, EnqueueStatus: {result.enqueue_status}"
                ),
                ip_address=get_remote_ip(websocket),
            )
            return {
                "source_run_id": str(result.source_run_id),
                "new_run_id": str(result.new_run_id),
                "schedule_id": int(result.schedule_id),
                "retried_records": int(result.retried_records),
                "skipped_records": int(result.skipped_records),
                "enqueue_status": result.enqueue_status,
                "records": [str(record_id) for record_id in result.enqueued_record_ids],
                "message": result.message,
            }

        try:
            response = await run_in_threadpool(_retry)
        except task_orchestration_service.ServiceError as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": exc.message},
            )
            return
        await task_realtime_service.task_realtime_hub.send(
            connection_id,
            {"type": "task_command_result", "action": action, "ok": True, **response},
        )
        return

    if action == "retry_selected":
        if not has_permission(user, "devices.backup"):
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "缺少 devices.backup 权限"},
            )
            return
        run_id_raw = payload.get("run_id")
        try:
            run_id = UUID(str(run_id_raw))
        except Exception:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "run_id 无效"},
            )
            return
        selected_backup_ids = _parse_selected_backup_ids(payload.get("backup_ids"))
        if not selected_backup_ids:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": "请选择至少一个任务"},
            )
            return

        def _retry_selected() -> dict[str, object]:
            with Session(engine, expire_on_commit=False) as session:
                result = task_orchestration_service.retry_selected_schedule_run(
                    session,
                    run_id=run_id,
                    backup_ids=selected_backup_ids,
                    allowed_group_ids=allowed_group_ids,
                )
                session.commit()
            _create_websocket_audit_log(
                user=user,
                action="RETRY_SCHEDULE_RUN_SELECTED",
                resource_type="schedule_run",
                resource_id=str(run_id),
                details=(
                    f"Source Run ID: {result.source_run_id}, New Run ID: {result.new_run_id}, "
                    f"Schedule ID: {result.schedule_id}, Selected: {result.selected_records}, "
                    f"Retried: {result.retried_records}, Skipped: {result.skipped_records}, "
                    f"EnqueueStatus: {result.enqueue_status}"
                ),
                ip_address=get_remote_ip(websocket),
            )
            return {
                "source_run_id": str(result.source_run_id),
                "new_run_id": str(result.new_run_id),
                "schedule_id": int(result.schedule_id),
                "selected_records": int(result.selected_records),
                "retried_records": int(result.retried_records),
                "skipped_records": int(result.skipped_records),
                "enqueue_status": result.enqueue_status,
                "records": [str(record_id) for record_id in result.enqueued_record_ids],
                "message": result.message,
            }

        try:
            response = await run_in_threadpool(_retry_selected)
        except task_orchestration_service.ServiceError as exc:
            await task_realtime_service.task_realtime_hub.send(
                connection_id,
                {"type": "task_command_result", "action": action, "ok": False, "message": exc.message},
            )
            return
        await task_realtime_service.task_realtime_hub.send(
            connection_id,
            {"type": "task_command_result", "action": action, "ok": True, **response},
        )
        return

    await task_realtime_service.task_realtime_hub.send(
        connection_id,
        {"type": "task_error", "message": f"不支持的动作: {action or 'unknown'}"},
    )


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


@router.post("/api/tasks/backups/query", summary="按追踪标识获取备份任务", description="根据批次 Run ID 或单条 Backup ID 查询备份任务")
def api_tasks_backups_query(request: Request, payload: BackupBatchQueryRequest, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    if payload.run_id:
        return backup_service.list_task_backups_for_run(
            session,
            run_id=payload.run_id,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
    if payload.backup_id:
        data = backup_service.get_task_backup(
            session,
            backup_id=payload.backup_id,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
        return {
            "found": bool(data.get("items")),
            "backup_id": str(payload.backup_id),
            "items": data.get("items") or [],
            "running": int(data.get("running") or 0),
        }
    raise HTTPException(status_code=400, detail="缺少 run_id 或 backup_id")


@router.get("/api/tasks/backups/recent", summary="获取最近备份任务", description="查询当前用户可见的最近活动或最近完成备份任务")
def api_tasks_backups_recent(request: Request, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    return backup_service.get_recent_task_tracking_payload(
        session,
        offset_minutes=offset_minutes,
        allowed_group_ids=allowed_group_ids,
    )


@router.websocket("/ws/tasks/backups")
async def ws_tasks_backups(websocket: WebSocket):
    token = websocket.cookies.get(settings.auth_cookie_name, "")
    payload = decode_session_token(token)
    user_id = int(payload.get("uid", 0)) if payload else 0
    if user_id <= 0:
        await websocket.close(code=4401)
        return

    context = await run_in_threadpool(_load_task_ws_context, user_id)
    if not context:
        await websocket.close(code=4403)
        return
    user = context["user"]
    if not has_permission(user, "backups.view"):
        await websocket.close(code=4403)
        return

    await websocket.accept()
    connection_id = await task_realtime_service.task_realtime_hub.register(websocket)
    await task_realtime_service.task_realtime_hub.send(
        connection_id,
        {
            "type": "hello",
            "channel": "task_control",
            "delivery_mode": "event_bus" if task_event_bus_service.event_bus_enabled() else "snapshot_polling",
            "capabilities": {
                "subscribe": True,
                "subscribe_logs": True,
                "unsubscribe": True,
                "unsubscribe_logs": True,
                "terminate_run": has_permission(user, "schedules.update"),
                "terminate_selected": has_permission(user, "schedules.update"),
                "retry_run": has_permission(user, "devices.backup"),
                "retry_selected": has_permission(user, "devices.backup"),
                "task_logs": True,
            },
        },
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                await task_realtime_service.task_realtime_hub.send(
                    connection_id,
                    {"type": "task_error", "message": "消息必须是 JSON"},
                )
                continue
            if not isinstance(data, dict):
                await task_realtime_service.task_realtime_hub.send(
                    connection_id,
                    {"type": "task_error", "message": "消息格式无效"},
                )
                continue
            await _handle_task_ws_message(
                websocket=websocket,
                connection_id=connection_id,
                payload=data,
                user=user,
                allowed_group_ids=context["allowed_group_ids"],
                tz_offset_minutes=int(context["tz_offset_minutes"] or 0),
                locale=str(getattr(user, "locale", settings.default_locale) or settings.default_locale),
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        await task_realtime_service.task_realtime_hub.send(
            connection_id,
            {"type": "task_error", "message": "任务通道异常关闭"},
        )
    finally:
        await task_realtime_service.task_realtime_hub.unregister(connection_id)


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


@router.get("/api/backups/{backup_id}/logs", summary="获取备份执行日志", description="查询指定备份记录关联的执行事件日志")
def api_backup_logs(request: Request, backup_id: UUID, limit: int = 300, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        return backup_service.get_backup_log_payload(
            session,
            backup_id,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
            limit=limit,
            locale=request.state.locale,
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
