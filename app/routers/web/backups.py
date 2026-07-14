from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app.db import get_session
from app.i18n import translate
from app.routers.support import _current_user, _log_action, _require_any_permission, _require_permission, get_user_allowed_group_ids
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import ConfigSearchListQueryInput
from app.services import backup_service


router = APIRouter(tags=["备份管理 (Backups)"])


@router.get("/diff-rules", summary="Diff规则页面", description="查看配置对比忽略规则")
def diff_rules_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(request, ["diff_rules.view", "diff_rules.update", "diff_rules.delete"])
    msg = (request.query_params.get("msg") or "").strip()
    err = (request.query_params.get("err") or "").strip()
    payload = backup_service.get_diff_rules_page_payload(session)
    return templates.TemplateResponse(
        request=request,
        name="diff_rules.html",
        context={
            **_layout_context(request=request, active="diff_rules"),
            "page_title": translate(request.state.locale, "nav.diff_rules"),
            "page_subtitle": translate(request.state.locale, "page.diff_rules.subtitle"),
            **payload,
            "msg": msg,
            "err": err,
        },
    )


@router.post("/diff-rules", summary="创建/更新Diff规则", description="新增或修改对比忽略规则")
def update_diff_rules(request: Request, rules_json: str = Form(""), session: Session = Depends(get_session)):
    _require_any_permission(request, ["diff_rules.update", "diff_rules.delete"])
    try:
        payload = json.loads(rules_json or "[]")
    except Exception:
        return RedirectResponse(url="/diff-rules?err=规则解析失败", status_code=303)
    normalized, required_permissions = backup_service.get_diff_rules_required_permissions(session, payload)
    for code in sorted(required_permissions):
        _require_permission(request, code)
    normalized = backup_service.save_diff_rules(session, normalized)
    change_types = ",".join(sorted(required_permissions)) or "none"
    _log_action(request, session, "UPDATE_DIFF_RULES", "settings", None, f"Rules: {len(normalized)}; perms={change_types}")
    return RedirectResponse(url="/diff-rules?msg=message.saved", status_code=303)


@router.get("/backups", summary="备份历史页面", description="查看全局设备备份记录")
def backups_page(request: Request, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    device_rows = backup_service.list_backup_page_rows(session, allowed_group_ids=allowed_group_ids)
    return templates.TemplateResponse(
        request=request,
        name="backups.html",
        context={**_layout_context(request=request, active="backups"), "device_rows": device_rows},
    )


@router.get("/config-search", summary="配置搜索页面", description="全文搜索设备的最新配置内容", tags=["备份管理 (Backups)"])
def config_search_page(
    request: Request,
    session: Session = Depends(get_session),
):
    _require_permission(request, "config_search.view")
    list_query = ConfigSearchListQueryInput.from_query_params(request.query_params)
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    payload = backup_service.get_config_search_payload(
        session,
        q=list_query.q or "",
        scope=list_query.scope,
        page=list_query.page,
        limit=list_query.limit,
        include_limit_param=list_query.include_limit_param,
        allowed_group_ids=allowed_group_ids,
    )

    return templates.TemplateResponse(
        request=request,
        name="config_search.html",
        context={
            **_layout_context(request=request, active="config_search"),
            "page_title": translate(request.state.locale, "nav.config_search"),
            "page_subtitle": translate(request.state.locale, "page.config_search.subtitle"),
            **payload,
        },
    )


@router.get("/tasks", summary="任务页面", description="查看任务状态")
def tasks_page(request: Request):
    return RedirectResponse(url="/backups", status_code=302)


@router.get("/backups/{backup_id}/download", summary="下载备份配置", description="下载指定的设备备份配置文件")
def download_backup(request: Request, backup_id: UUID, session: Session = Depends(get_session)):
    _require_permission(request, "backups.view")
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)
    try:
        payload = backup_service.get_backup_download_payload(
            session,
            backup_id,
            offset_minutes=offset_minutes,
            allowed_group_ids=allowed_group_ids,
        )
    except backup_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return Response(
        content=payload["content_bytes"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": payload["content_disposition"]},
    )
