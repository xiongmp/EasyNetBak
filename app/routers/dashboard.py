from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import select

from app import crud
from app.db import session_scope
from app.models import Device
from app.routers.common import _current_user, _layout_context, _require_permission, templates


router = APIRouter(tags=["仪表盘 (Dashboard)"])


@router.get("/", summary="重定向至仪表盘", description="根路径自动重定向到 /dashboard")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", summary="仪表盘页面", description="展示系统运行概览、平台统计、备份趋势、设备健康状态及最近备份记录")
def dashboard_page(request: Request):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    _require_permission(request, "dashboard.view")

    with session_scope() as session:
        summary = crud.get_dashboard_summary(session)
        platform_stats = crud.get_device_platform_stats(session)
        trend_stats = crud.get_backup_trend_stats(session, days=30)
        change_heatmap = crud.get_config_change_heatmap_stats(session, days=90)
        health_stats = crud.get_group_health_stats(session)
        recent_backups = crud.get_latest_backups_per_device(session)

        device_ids = {r.device_id for r in recent_backups}
        device_map = {}
        if device_ids:
            devices = session.exec(select(Device).where(Device.id.in_(list(device_ids)))).all()
            for d in devices:
                if d.id is not None:
                    device_map[d.id] = d

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            **_layout_context(request=request, active="dashboard"),
            "page_title": "仪表盘",
            "page_subtitle": "系统运行概览与统计分析",
            "summary": summary,
            "platform_stats": platform_stats,
            "trend_stats": trend_stats,
            "change_heatmap": change_heatmap,
            "health_stats": health_stats,
            "recent_backups": recent_backups,
            "device_map": device_map,
        },
    )


@router.get("/@vite/client", include_in_schema=False)
def _vite_client() -> Response:
    return Response(status_code=204)
