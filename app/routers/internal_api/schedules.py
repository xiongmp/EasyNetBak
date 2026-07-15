from __future__ import annotations

from uuid import UUID

from app import crud


from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.db import get_session
from app.i18n import translate
from app.routers.support import _current_user, _log_action, _require_any_permission, _require_permission, get_user_allowed_group_ids
from app.scheduler import resolve_schedule_device_ids, sync_scheduler_from_db
from app.services import backup_service, schedule_service, task_event_bus_service, task_orchestration_service


router = APIRouter(tags=["定时任务 (Schedules)"])


class ScheduleRunSelectionRequest(BaseModel):
    backup_ids: list[UUID]


@router.post("/api/schedules/{schedule_id}/run", summary="手动触发任务", description="立即执行指定的定时任务")
def api_run_schedule(request: Request, schedule_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "schedules.update")
    _require_permission(request, "devices.backup")
    schedule = crud.get_schedule(session, int(schedule_id))
    if schedule is None:
        raise HTTPException(status_code=404, detail=translate(request.state.locale, "error.schedule.not_found"))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    device_ids = resolve_schedule_device_ids(
        session,
        schedule=schedule,
        allowed_group_ids=allowed_group_ids,
    )
    if not device_ids:
        return {
            "run_id": None,
            "records": [],
            "enqueue_status": "none",
            "reason": "no_devices",
        }
    try:
        run_id, jobs = task_orchestration_service.plan_schedule_run(
            session,
            schedule_id=int(schedule_id),
            trigger="manual",
            device_ids=device_ids,
        )
    except task_orchestration_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _log_action(request, session, "TRIGGER_SCHEDULE_API", "schedule", schedule_id, f"Run ID: {run_id}, Jobs: {len(jobs)}")
    enqueue_result = backup_service.enqueue_schedule_jobs(
        session,
        run_id=run_id,
        jobs=jobs,
    )
    if not enqueue_result.enqueued and enqueue_result.enqueue_error_message:
        raise HTTPException(status_code=503, detail=enqueue_result.enqueue_error_message)
    if enqueue_result.enqueue_status == "none":
        raise HTTPException(status_code=503, detail=translate(request.state.locale, "task.command.queue_unavailable"))
    return {
        "run_id": str(run_id),
        "records": [str(record_id) for record_id in enqueue_result.enqueued_record_ids],
        "enqueue_status": enqueue_result.enqueue_status,
        "enqueue_warning_message": enqueue_result.enqueue_warning_message,
    }


@router.post("/api/schedules/{schedule_id}/toggle", summary="启停定时任务", description="启用或禁用指定的定时任务")
def api_toggle_schedule(request: Request, schedule_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "schedules.update")
    try:
        new_status = schedule_service.toggle_schedule(session, schedule_id)
    except schedule_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _log_action(request, session, "TOGGLE_SCHEDULE", "schedule", schedule_id, f"Enabled: {new_status}")

    session.commit()
    sync_scheduler_from_db()
    try:
        next_run = schedule_service.get_schedule_next_run_payload(
            session,
            schedule_id=int(schedule_id),
            locale=request.state.locale,
        )
    except schedule_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"success": True, "enabled": new_status, "next_run": next_run}


@router.get("/api/schedules/{schedule_id}/stats/runs", summary="获取任务运行记录", description="查询定时任务的实时运行状态列表")
def api_schedule_stats_runs(request: Request, schedule_id: int, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["schedules.view", "schedules.create", "schedules.update", "schedules.delete"],
    )
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    try:
        return schedule_service.get_schedule_runs_live_payload(
            session,
            schedule_id=int(schedule_id),
            offset_minutes=offset_minutes,
            limit=30,
            locale=request.state.locale,
        )
    except schedule_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/api/schedules/runs/{run_id}/terminate", summary="终止未运行任务", description="仅终止指定运行中尚未开始执行的子任务")
def api_terminate_schedule_run(request: Request, run_id: UUID, session: Session = Depends(get_session)):
    _require_permission(request, "schedules.update")
    try:
        result = task_orchestration_service.terminate_schedule_run(
            session,
            run_id=run_id,
            locale=request.state.locale,
        )
    except task_orchestration_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _log_action(
        request,
        session,
        "TERMINATE_SCHEDULE_RUN_PENDING",
        "schedule_run",
        str(run_id),
        (
            f"Schedule ID: {result.schedule_id}, Status: {result.status}, "
            f"Terminated: {result.terminated_records}, Skipped: {result.skipped_records}, "
            f"Running: {result.running_records}"
        ),
    )
    task_event_bus_service.publish_task_state_hint(run_id=str(result.run_id), event="terminate_schedule_run")
    return {
        "success": True,
        "run_id": str(result.run_id),
        "schedule_id": int(result.schedule_id),
        "status": result.status,
        "terminated_records": int(result.terminated_records),
        "skipped_records": int(result.skipped_records),
        "running_records": int(result.running_records),
        "message": result.message,
    }


@router.post("/api/schedules/runs/{run_id}/terminate-selected", summary="终止所选未运行任务", description="仅终止指定运行中选中的、尚未开始执行的子任务")
def api_terminate_selected_schedule_run(
    request: Request,
    run_id: UUID,
    payload: ScheduleRunSelectionRequest,
    session: Session = Depends(get_session),
):
    _require_permission(request, "schedules.update")
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        result = task_orchestration_service.terminate_selected_schedule_run(
            session,
            run_id=run_id,
            backup_ids=list(payload.backup_ids or []),
            allowed_group_ids=allowed_group_ids,
            locale=request.state.locale,
        )
    except task_orchestration_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _log_action(
        request,
        session,
        "TERMINATE_SCHEDULE_RUN_SELECTED",
        "schedule_run",
        str(run_id),
        (
            f"Schedule ID: {result.schedule_id}, Status: {result.status}, "
            f"Selected: {result.selected_records}, Terminated: {result.terminated_records}, "
            f"Skipped: {result.skipped_records}, Running: {result.running_records}"
        ),
    )
    task_event_bus_service.publish_task_state_hint(
        run_id=str(result.run_id),
        event="terminate_selected_schedule_run",
        details={"record_ids": [str(backup_id) for backup_id in payload.backup_ids]},
    )
    return {
        "success": True,
        "run_id": str(result.run_id),
        "schedule_id": int(result.schedule_id),
        "status": result.status,
        "selected_records": int(result.selected_records),
        "terminated_records": int(result.terminated_records),
        "skipped_records": int(result.skipped_records),
        "running_records": int(result.running_records),
        "message": result.message,
    }


@router.post("/api/schedules/runs/{run_id}/retry", summary="重试运行任务", description="重试指定运行中未成功的设备任务，并创建新的运行批次")
def api_retry_schedule_run(request: Request, run_id: UUID, session: Session = Depends(get_session)):
    _require_permission(request, "devices.backup")
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        result = task_orchestration_service.retry_schedule_run(
            session,
            run_id=run_id,
            allowed_group_ids=allowed_group_ids,
            locale=request.state.locale,
        )
    except task_orchestration_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _log_action(
        request,
        session,
        "RETRY_SCHEDULE_RUN",
        "schedule_run",
        str(run_id),
        (
            f"Source Run ID: {result.source_run_id}, New Run ID: {result.new_run_id}, "
            f"Schedule ID: {result.schedule_id}, Retried: {result.retried_records}, "
            f"Skipped: {result.skipped_records}, EnqueueStatus: {result.enqueue_status}"
        ),
    )
    return {
        "success": True,
        "source_run_id": str(result.source_run_id),
        "new_run_id": str(result.new_run_id),
        "schedule_id": int(result.schedule_id),
        "retried_records": int(result.retried_records),
        "skipped_records": int(result.skipped_records),
        "enqueue_status": result.enqueue_status,
        "records": [str(record_id) for record_id in result.enqueued_record_ids],
        "message": result.message,
    }


@router.post("/api/schedules/runs/{run_id}/retry-selected", summary="重试所选任务", description="仅重试指定运行中选中的失败或已终止任务，并创建新的运行批次")
def api_retry_selected_schedule_run(
    request: Request,
    run_id: UUID,
    payload: ScheduleRunSelectionRequest,
    session: Session = Depends(get_session),
):
    _require_permission(request, "devices.backup")
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        result = task_orchestration_service.retry_selected_schedule_run(
            session,
            run_id=run_id,
            backup_ids=list(payload.backup_ids or []),
            allowed_group_ids=allowed_group_ids,
            locale=request.state.locale,
        )
    except task_orchestration_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _log_action(
        request,
        session,
        "RETRY_SCHEDULE_RUN_SELECTED",
        "schedule_run",
        str(run_id),
        (
            f"Source Run ID: {result.source_run_id}, New Run ID: {result.new_run_id}, "
            f"Schedule ID: {result.schedule_id}, Selected: {result.selected_records}, "
            f"Retried: {result.retried_records}, Skipped: {result.skipped_records}, "
            f"EnqueueStatus: {result.enqueue_status}"
        ),
    )
    return {
        "success": True,
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


@router.get("/api/schedules/targets/groups", summary="获取任务目标分组", description="查询定时任务的候选设备分组")
def api_schedule_target_groups(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    return schedule_service.list_schedule_target_groups(
        session,
        allowed_group_ids=allowed_group_ids,
    )


@router.get("/api/schedules/targets/platforms", summary="获取任务目标平台", description="查询定时任务的候选设备平台")
def api_schedule_target_platforms(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    return schedule_service.list_schedule_target_platforms(session)


@router.get("/api/schedules/targets/devices", summary="获取任务目标设备", description="查询定时任务的候选设备")
def api_schedule_target_devices(
    request: Request,
    session: Session = Depends(get_session),
    q: str = "",
    platform: str = "",
    group_id: int = 0,
    limit: int = 80,
):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    q = (q or "").strip() or None
    platform = (platform or "").strip() or None
    group_id_val = int(group_id) if int(group_id or 0) > 0 else None
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)

    return schedule_service.list_schedule_target_devices(
        session,
        q=q,
        platform=platform,
        group_id=group_id_val,
        limit=limit,
        allowed_group_ids=allowed_group_ids,
    )


@router.post("/api/schedules/preview", summary="预览任务目标", description="预览定时任务将会备份的设备列表")
def api_schedule_preview(request: Request, targets: str = Form(""), session: Session = Depends(get_session)):
    _require_any_permission(request, ["schedules.create", "schedules.update"])
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    return schedule_service.preview_schedule_targets(
        session,
        targets=targets,
        allowed_group_ids=allowed_group_ids,
    )
