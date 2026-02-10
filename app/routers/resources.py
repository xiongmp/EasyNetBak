from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import crud
from app.db import session_scope
from app.models import BackupTemplate, Credential
from app.platforms import DEFAULT_COMMANDS, PLATFORMS, normalize_platform_id
from app.routers.common import _layout_context, _log_action, _require_admin, _require_operator, templates


router = APIRouter()


@router.get("/credentials")
def credentials_page(request: Request):
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        total = crud.count_credentials(session)
        items = crud.list_credentials(session, limit=limit, offset=offset)
        usage = {c.id: crud.credential_usage_count(session, c.id) for c in items if c.id}
        edit_id = request.query_params.get("edit")
        current = None
        if edit_id and edit_id.isdigit():
            current = crud.get_credential(session, int(edit_id))
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/credentials?limit={limit}&page="
    if not request.query_params.get("limit"):
         # 如果 URL 中没有 limit，pagination_base 就不带 limit，除非 page_size 不是默认的
         if limit != 10:
             pagination_base = f"/credentials?limit={limit}&page="
         else:
             pagination_base = "/credentials?page="

    return templates.TemplateResponse(
        "credentials.html",
        {
            **_layout_context(request=request, active="credentials"), 
            "items": items, 
            "usage": usage, 
            "current": current,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )


@router.post("/credentials")
def create_credential(
    request: Request,
    credential_id: int = Form(0),
    name: str = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    enable_password: str = Form(""),
    remarks: str = Form(""),
):
    _require_operator(request)
    with session_scope() as session:
        if credential_id and int(credential_id) > 0:
            crud.update_credential(
                session,
                int(credential_id),
                name=name,
                username=username,
                password=password,
                enable_password=enable_password,
                remarks=remarks.strip() or None,
            )
            _log_action(request, session, "UPDATE_CREDENTIAL", "credential", credential_id, f"Name: {name}")
        else:
            cred = Credential(
                name=name.strip(),
                username=username.strip(),
                password=password,
                enable_password=enable_password,
                remarks=remarks.strip() or None,
            )
            cred = crud.create_credential(session, credential=cred)
            _log_action(request, session, "CREATE_CREDENTIAL", "credential", cred.id, f"Name: {name}")
    return RedirectResponse(url="/credentials?msg=已保存", status_code=303)


@router.post("/credentials/{credential_id}/delete")
def delete_credential(request: Request, credential_id: int):
    _require_operator(request)
    with session_scope() as session:
        cred = crud.get_credential(session, credential_id)
        name = cred.name if cred else f"ID: {credential_id}"
        try:
            crud.delete_credential(session, credential_id)
            _log_action(request, session, "DELETE_CREDENTIAL", "credential", credential_id, f"Name: {name}")
        except RuntimeError as exc:
            return RedirectResponse(url=f"/credentials?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/credentials?msg=已删除", status_code=303)


@router.get("/groups")
def groups_page(request: Request):
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        total = crud.count_groups(session)
        items = crud.list_groups(session, limit=limit, offset=offset)
        usage = {g.id: crud.group_usage_count(session, g.id) for g in items if g.id}
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/groups?limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/groups?limit={limit}&page="
         else:
             pagination_base = "/groups?page="

    return templates.TemplateResponse(
        "groups.html",
        {
            **_layout_context(request=request, active="groups"), 
            "items": items, 
            "usage": usage,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )


@router.post("/groups")
def create_group(
    request: Request,
    group_id: int = Form(0),
    name: str = Form(...),
):
    _require_operator(request)
    with session_scope() as session:
        if group_id and int(group_id) > 0:
            crud.update_group(session, int(group_id), name=name)
            _log_action(request, session, "UPDATE_GROUP", "group", group_id, f"Name: {name}")
        else:
            group = crud.create_group(session, name=name)
            _log_action(request, session, "CREATE_GROUP", "group", group.id, f"Name: {name}")
    return RedirectResponse(url="/groups?msg=已保存", status_code=303)


@router.post("/groups/{group_id}/delete")
def delete_group(request: Request, group_id: int):
    _require_operator(request)
    with session_scope() as session:
        group = crud.get_group(session, group_id)
        name = group.name if group else f"ID: {group_id}"
        try:
            crud.delete_group(session, group_id)
            _log_action(request, session, "DELETE_GROUP", "group", group_id, f"Name: {name}")
        except RuntimeError as exc:
            return RedirectResponse(url=f"/groups?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/groups?msg=已删除", status_code=303)


@router.get("/templates")
def templates_page(request: Request):
    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    limit = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if limit > 100:
        limit = 100
    offset = (page - 1) * limit

    with session_scope() as session:
        total = crud.count_templates(session)
        items = crud.list_templates(session, limit=limit, offset=offset)
        edit_id = request.query_params.get("edit")
        current = None
        if edit_id and edit_id.isdigit():
            current = crud.get_template(session, int(edit_id))
    
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_base = f"/templates?limit={limit}&page="
    if not request.query_params.get("limit"):
         if limit != 10:
             pagination_base = f"/templates?limit={limit}&page="
         else:
             pagination_base = "/templates?page="

    default_platforms = PLATFORMS
    return templates.TemplateResponse(
        "templates.html",
        {
            **_layout_context(request=request, active="templates"),
            "items": items,
            "default_commands": DEFAULT_COMMANDS,
            "default_platforms": default_platforms,
            "current": current,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
            },
            "pagination_base": pagination_base,
        },
    )


@router.post("/templates")
def create_template(
    request: Request,
    template_id: int = Form(0),
    name: str = Form(...),
    platform: str = Form(...),
    commands: str = Form(""),
):
    _require_operator(request)
    commands = (commands or "").strip() or DEFAULT_COMMANDS.get(normalize_platform_id(platform), "")
    with session_scope() as session:
        if template_id and int(template_id) > 0:
            crud.update_template(
                session,
                int(template_id),
                name=name.strip(),
                platform=platform,
                commands=commands,
            )
            _log_action(request, session, "UPDATE_TEMPLATE", "template", template_id, f"Name: {name}")
        else:
            tpl = crud.create_template(session, template=BackupTemplate(name=name.strip(), platform=platform, commands=commands))
            _log_action(request, session, "CREATE_TEMPLATE", "template", tpl.id, f"Name: {name}")
    return RedirectResponse(url="/templates?msg=已保存", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(request: Request, template_id: int):
    _require_operator(request)
    with session_scope() as session:
        tpl = crud.get_template(session, template_id)
        name = tpl.name if tpl else f"ID: {template_id}"
        crud.delete_template(session, template_id)
        _log_action(request, session, "DELETE_TEMPLATE", "template", template_id, f"Name: {name}")
    return RedirectResponse(url="/templates?msg=已删除", status_code=303)
