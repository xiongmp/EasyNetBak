from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session
from sqlmodel import select

from app import crud
from app.db import get_session
from app.models import Device
from app.routers.support import _current_user, _require_permission
from app.routers.web_context import _layout_context, templates
from app.services import task_observability_service


router = APIRouter(tags=["仪表盘 (Dashboard)"])
_DASHBOARD_WINDOW_OPTIONS: dict[str, dict[str, int | str]] = {
    "24h": {"label": "最近24小时", "hours": 24, "days": 1},
    "7d": {"label": "最近7天", "hours": 24 * 7, "days": 7},
    "30d": {"label": "最近30天", "hours": 24 * 30, "days": 30},
}


def _resolve_dashboard_window(raw_window: str | None) -> dict[str, int | str]:
    key = (raw_window or "7d").strip().lower()
    return _DASHBOARD_WINDOW_OPTIONS.get(key, _DASHBOARD_WINDOW_OPTIONS["7d"])


@router.get("/", summary="重定向至仪表盘", description="根路径自动重定向到 /dashboard")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", summary="仪表盘页面", description="展示系统运行概览、平台统计、备份趋势、设备健康状态及最近备份记录")
def dashboard_page(request: Request, session: Session = Depends(get_session), window: str = "7d"):
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    _require_permission(request, "dashboard.view")

    selected_key = (window or "7d").strip().lower()
    if selected_key not in _DASHBOARD_WINDOW_OPTIONS:
        selected_key = "7d"
    selected_window = _resolve_dashboard_window(selected_key)
    summary = crud.get_dashboard_summary(session, window_hours=int(selected_window["hours"]))
    platform_stats = crud.get_device_platform_stats(session)
    trend_stats = crud.get_backup_trend_stats(session, window_key=selected_key)
    change_heatmap = crud.get_config_change_heatmap_stats(session, window_key=selected_key)
    health_stats = crud.get_group_health_stats(session, window_days=int(selected_window["days"]))
    recent_backups = crud.get_latest_backups_per_device(session)
    task_health = task_observability_service.get_task_health_snapshot(
        session,
        window_hours=int(selected_window["hours"]),
    )

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
            "task_health": task_health,
            "dashboard_window": {
                "key": selected_key,
                "label": str(selected_window["label"]),
                "hours": int(selected_window["hours"]),
                "days": int(selected_window["days"]),
            },
            "dashboard_window_options": [
                {"key": key, **value}
                for key, value in _DASHBOARD_WINDOW_OPTIONS.items()
            ],
        },
    )


@router.get("/@vite/client", include_in_schema=False)
def _vite_client() -> Response:
    return Response(status_code=204)
