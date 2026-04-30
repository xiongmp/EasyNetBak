from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.routers.support import _log_action, _require_any_permission, _require_permission
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import EditableListQueryInput
from app.scheduler import sync_scheduler_from_db
from app.services import schedule_service


router = APIRouter(tags=["定时任务 (Schedules)"])


@router.get("/schedules", summary="定时任务页面", description="查看自动化备份计划任务列表")
def schedules_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["schedules.view", "schedules.create", "schedules.update", "schedules.delete"],
    )
    list_query = EditableListQueryInput.from_query_params(request.query_params)

    payload = schedule_service.get_schedule_page_payload(
        session,
        page=list_query.page,
        limit=list_query.limit,
        edit_id=list_query.edit,
        include_limit_param=list_query.include_limit_param,
    )

    return templates.TemplateResponse(
        request=request,
        name="schedules.html",
        context={
            **_layout_context(request=request, active="schedules"),
            **payload,
        },
    )


@router.post("/schedules", summary="创建或更新定时任务", description="新增或修改定时备份任务")
def upsert_schedule(
    request: Request,
    session: Session = Depends(get_session),
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
    is_enabled = enabled in {"1", "true", "True", "yes", "YES", "on"}

    try:
        schedule = schedule_service.upsert_schedule(
            session,
            schedule_id=int(schedule_id or 0),
            name=name,
            crontab=crontab,
            enabled=is_enabled,
            targets=targets,
        )
    except schedule_service.ServiceError as exc:
        return RedirectResponse(url=f"/schedules?err={exc.message}", status_code=303)
    if schedule_id and int(schedule_id) > 0:
        _log_action(request, session, "UPDATE_SCHEDULE", "schedule", schedule_id, f"Name: {name}, Enabled: {is_enabled}")
    else:
        _log_action(request, session, "CREATE_SCHEDULE", "schedule", schedule.id, f"Name: {name}, Enabled: {is_enabled}")

    session.commit()
    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=已保存", status_code=303)


@router.post("/schedules/{schedule_id}/delete", summary="删除定时任务", description="删除指定的定时任务")
def delete_schedule(request: Request, schedule_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "schedules.delete")
    name = schedule_service.delete_schedule(session, int(schedule_id))
    _log_action(request, session, "DELETE_SCHEDULE", "schedule", schedule_id, f"Name: {name}")
    session.commit()
    sync_scheduler_from_db()
    return RedirectResponse(url="/schedules?msg=已删除", status_code=303)


@router.get("/schedules/{schedule_id}/stats", summary="任务统计", description="查看定时任务的执行统计数据")
def schedule_stats_page(request: Request, schedule_id: int, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["schedules.view", "schedules.create", "schedules.update", "schedules.delete"],
    )
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    payload = schedule_service.get_schedule_stats_payload(
        session,
        schedule_id=int(schedule_id),
        offset_minutes=offset_minutes,
    )

    return templates.TemplateResponse(
        request=request,
        name="schedule_stats.html",
        context={
            **_layout_context(request=request, active="schedules"),
            **payload,
        },
    )
