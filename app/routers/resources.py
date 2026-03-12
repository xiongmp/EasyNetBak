from __future__ import annotations

import csv
import io
from urllib.parse import quote
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlmodel import select

from app import crud
from app.db import session_scope
from app.models import BackupTemplate, Credential
from app.platforms import DEFAULT_COMMANDS, PLATFORMS, normalize_platform_id
from app.routers.common import _layout_context, _log_action, _require_permission, templates


router = APIRouter()


@router.get("/credentials")
def credentials_page(request: Request):
    _require_permission(request, "credentials.view")
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
    if credential_id and int(credential_id) > 0:
        _require_permission(request, "credentials.update")
    else:
        _require_permission(request, "credentials.create")
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
    _require_permission(request, "credentials.delete")
    with session_scope() as session:
        cred = crud.get_credential(session, credential_id)
        name = cred.name if cred else f"ID: {credential_id}"
        try:
            crud.delete_credential(session, credential_id)
            _log_action(request, session, "DELETE_CREDENTIAL", "credential", credential_id, f"Name: {name}")
        except RuntimeError as exc:
            return RedirectResponse(url=f"/credentials?err={str(exc)}", status_code=303)
    return RedirectResponse(url="/credentials?msg=已删除", status_code=303)


@router.get("/credentials/import_template.csv")
def download_credential_import_template(request: Request):
    _require_permission(request, "credentials.create")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "username", "password", "enable_password", "remarks"])
    w.writerow(["核心交换机-只读", "readonly", "pass123", "", "NOC 账号"])
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="credential_import_template.csv"'},
    )


@router.post("/credentials/import.csv")
async def import_credentials_csv(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("insert"),
):
    mode = (mode or "insert").strip()
    if mode not in {"insert", "upsert"}:
        mode = "insert"
    if mode == "upsert":
        _require_permission(request, "credentials.update")
    else:
        _require_permission(request, "credentials.create")
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return RedirectResponse(url="/credentials?err=请上传CSV文件", status_code=303)
    content = await file.read()
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if text is None:
        text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"name", "username"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return RedirectResponse(url="/credentials?err=CSV缺少必要列", status_code=303)

    created = 0
    updated = 0
    skipped = 0
    with session_scope() as session:
        for idx, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            username = (row.get("username") or "").strip()
            password_raw = row.get("password")
            enable_raw = row.get("enable_password")
            remarks_raw = row.get("remarks")

            if not name or not username:
                skipped += 1
                continue

            password_val = (password_raw or "").strip()
            enable_val = (enable_raw or "").strip()
            remarks_val = (remarks_raw or "").strip() or None

            existing = session.exec(select(Credential).where(Credential.name == name)).first()
            if existing:
                if mode == "upsert":
                    password_update = password_val if password_val else None
                    enable_update = enable_val if enable_val else None
                    crud.update_credential(
                        session,
                        existing.id,
                        name=name,
                        username=username,
                        password=password_update,
                        enable_password=enable_update,
                        remarks=remarks_val,
                    )
                    _log_action(
                        request,
                        session,
                        "UPDATE_CREDENTIAL",
                        "credential",
                        existing.id,
                        f"Name: {name} (Import)",
                    )
                    updated += 1
                else:
                    skipped += 1
                continue

            cred = Credential(
                name=name,
                username=username,
                password=password_val if password_val else None,
                enable_password=enable_val if enable_val else None,
                remarks=remarks_val,
            )
            cred = crud.create_credential(session, credential=cred)
            _log_action(request, session, "CREATE_CREDENTIAL", "credential", cred.id, f"Name: {name} (Import)")
            created += 1

    msg = f"导入完成：创建{created}，更新{updated}，跳过{skipped}"
    return RedirectResponse(url=f"/credentials?msg={quote(msg)}", status_code=303)


@router.get("/groups")
def groups_page(request: Request):
    _require_permission(request, "groups.view")
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
    if group_id and int(group_id) > 0:
        _require_permission(request, "groups.update")
    else:
        _require_permission(request, "groups.create")
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
    try:
        _require_permission(request, "groups.delete")
        with session_scope() as session:
            # Check if group is in use
            usage = crud.group_usage_count(session, group_id)
            if usage > 0:
                msg = f"无法删除：该分组包含 {usage} 台设备，请先移除设备"
                return RedirectResponse(url=f"/groups?err={quote(msg)}", status_code=303)

            group = crud.get_group(session, group_id)
            if not group:
                return RedirectResponse(url="/groups?err=分组不存在", status_code=303)
            
            name = group.name
            try:
                crud.delete_group(session, group_id)
                _log_action(request, session, "DELETE_GROUP", "group", group_id, f"Name: {name}")
            except Exception as exc:
                msg = f"删除失败: {str(exc)}"
                return RedirectResponse(url=f"/groups?err={quote(msg)}", status_code=303)
                
        return RedirectResponse(url="/groups?msg=已删除", status_code=303)
    except Exception as exc:
        msg = f"操作失败: {str(exc)}"
        return RedirectResponse(url=f"/groups?err={quote(msg)}", status_code=303)


@router.get("/templates")
def templates_page(request: Request):
    _require_permission(request, "templates.view")
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
    if template_id and int(template_id) > 0:
        _require_permission(request, "templates.update")
    else:
        _require_permission(request, "templates.create")
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
    _require_permission(request, "templates.delete")
    with session_scope() as session:
        tpl = crud.get_template(session, template_id)
        name = tpl.name if tpl else f"ID: {template_id}"
        crud.delete_template(session, template_id)
        _log_action(request, session, "DELETE_TEMPLATE", "template", template_id, f"Name: {name}")
    return RedirectResponse(url="/templates?msg=已删除", status_code=303)
