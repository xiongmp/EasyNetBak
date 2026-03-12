from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import crud
from app.db import session_scope
from app.routers.common import _current_user, _layout_context, _require_permission, templates


router = APIRouter()


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard")
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
        recent_backups = crud.list_backups(session, limit=50)

        device_ids = {r.device_id for r in recent_backups}
        device_map = {}
        for did in device_ids:
            d = crud.get_device(session, did)
            if d:
                device_map[did] = d

    return templates.TemplateResponse(
        "dashboard.html",
        {
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


@router.get("/@vite/client")
def _vite_client() -> Response:
    return Response(status_code=204)
