from __future__ import annotations

import csv
import io
from urllib.parse import quote, urlencode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app import crud
from app.db import get_session
from app.models import Credential
from app.platforms import DEFAULT_COMMANDS, PLATFORMS
from app.routers.support import _log_action, _require_any_permission, _require_permission
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import BaseListQueryInput, EditableListQueryInput
from app.services import pagination_service, resource_service


router = APIRouter(tags=["资源管理 (Resources)"])


@router.get("/credentials", summary="凭据管理页面", description="查看设备登录凭据列表")
def credentials_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["credentials.view", "credentials.create", "credentials.update", "credentials.delete"],
    )
    list_query = EditableListQueryInput.from_query_params(request.query_params)
    pagination_params = pagination_service.normalize_pagination_params(
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )

    total = crud.count_credentials(session)
    items = crud.list_credentials(session, limit=pagination_params.limit, offset=pagination_params.offset)
    usage = {c.id: crud.credential_usage_count(session, c.id) for c in items if c.id}
    current = None
    if list_query.edit and list_query.edit.isdigit():
        current = crud.get_credential(session, int(list_query.edit))

    pagination = pagination_service.build_pagination_data(
        page=pagination_params.page,
        limit=pagination_params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/credentials",
        params={},
        limit=pagination.limit,
        limit_explicit=pagination_params.limit_explicit,
    )

    return templates.TemplateResponse(
        request=request,
        name="credentials.html",
        context={
            **_layout_context(request=request, active="credentials"), 
            "items": items, 
            "usage": usage, 
            "current": current,
            "pagination": pagination.as_dict(),
            "pagination_base": pagination_base,
        },
    )


@router.post("/credentials", summary="创建或更新凭据", description="新增或修改登录凭据信息")
def create_credential(
    request: Request,
    session: Session = Depends(get_session),
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
    try:
        if credential_id and int(credential_id) > 0:
            cred = resource_service.update_credential(
                session,
                int(credential_id),
                resource_service.CredentialUpdateInput(
                    name=name,
                    username=username,
                    password=password,
                    enable_password=enable_password,
                    remarks=remarks.strip() or None,
                ),
            )
            _log_action(request, session, "UPDATE_CREDENTIAL", "credential", cred.id, f"Name: {cred.name}")
        else:
            cred = resource_service.create_credential(
                session,
                resource_service.CredentialCreateInput(
                    name=name,
                    username=username,
                    password=password,
                    enable_password=enable_password,
                    remarks=remarks.strip() or None,
                ),
            )
            _log_action(request, session, "CREATE_CREDENTIAL", "credential", cred.id, f"Name: {cred.name}")
    except resource_service.ServiceError as exc:
        return RedirectResponse(url=f"/credentials?err={quote(exc.message)}", status_code=303)
    return RedirectResponse(url="/credentials?msg=message.saved", status_code=303)


@router.post("/credentials/{credential_id}/delete", summary="删除凭据", description="删除指定的登录凭据")
def delete_credential(request: Request, credential_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "credentials.delete")
    try:
        cred = resource_service.delete_credential(session, credential_id)
        _log_action(request, session, "DELETE_CREDENTIAL", "credential", credential_id, f"Name: {cred.name}")
    except resource_service.ServiceError as exc:
        return RedirectResponse(url=f"/credentials?err={quote(exc.message)}", status_code=303)
    return RedirectResponse(url="/credentials?msg=message.deleted", status_code=303)


@router.get("/credentials/import_template.csv", summary="下载凭据导入模板", description="获取凭据导入的CSV模板文件")
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


@router.post("/credentials/import.csv", summary="导入凭据", description="通过CSV批量导入登录凭据")
async def import_credentials_csv(
    request: Request,
    session: Session = Depends(get_session),
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
        return RedirectResponse(url="/credentials?err=credential_import.error.csv_required", status_code=303)
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
        return RedirectResponse(url="/credentials?err=credential_import.error.invalid_columns", status_code=303)

    created = 0
    updated = 0
    skipped = 0
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

    params = urlencode(
        {
            "msg": "message.credentials_imported",
            "created": created,
            "updated": updated,
            "skipped": skipped,
        }
    )
    return RedirectResponse(url=f"/credentials?{params}", status_code=303)


@router.get("/groups", summary="设备组页面", description="查看设备分组列表")
def groups_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(request, ["groups.view", "groups.create", "groups.update", "groups.delete"])
    
    # 获取全部分组树
    tree = resource_service.list_group_tree(session)
    
    # 扁平化树结构用于表格展示
    flat_items = []
    def _flatten(nodes):
        for node in nodes:
            flat_items.append(node)
            if node.get("children"):
                _flatten(node["children"])
    _flatten(tree)
    
    usage = {g["id"]: crud.group_usage_count(session, g["id"]) for g in flat_items if g.get("id")}

    return templates.TemplateResponse(
        request=request,
        name="groups.html",
        context={
            **_layout_context(request=request, active="groups"), 
            "items": flat_items, 
            "usage": usage,
        },
    )


@router.post("/groups", summary="创建或更新设备组", description="新增或修改设备分组")
def create_group(
    request: Request,
    session: Session = Depends(get_session),
    group_id: int = Form(0),
    name: str = Form(...),
    parent_id: int = Form(0),
):
    if group_id and int(group_id) > 0:
        _require_permission(request, "groups.update")
    else:
        _require_permission(request, "groups.create")
    try:
        parent_id_val = parent_id if parent_id > 0 else None
        if group_id and int(group_id) > 0:
            group = resource_service.update_group(
                session,
                int(group_id),
                resource_service.GroupUpdateInput(name=name, parent_id=parent_id_val),
            )
            _log_action(request, session, "UPDATE_GROUP", "group", group.id, f"Name: {group.name}")
        else:
            group = resource_service.create_group(
                session,
                resource_service.GroupCreateInput(name=name, parent_id=parent_id_val),
            )
            _log_action(request, session, "CREATE_GROUP", "group", group.id, f"Name: {group.name}")
    except resource_service.ServiceError as exc:
        return RedirectResponse(url=f"/groups?err={quote(exc.message)}", status_code=303)
    return RedirectResponse(url="/groups?msg=message.saved", status_code=303)


@router.post("/groups/{group_id}/delete", summary="删除设备组", description="删除指定的设备分组")
def delete_group(request: Request, group_id: int, session: Session = Depends(get_session)):
    try:
        _require_permission(request, "groups.delete")
        try:
            group = resource_service.delete_group(session, group_id)
            _log_action(request, session, "DELETE_GROUP", "group", group_id, f"Name: {group.name}")
        except resource_service.ServiceError as exc:
            if exc.code == "RESOURCE_GROUP_IN_USE":
                usage = int(exc.context.get("usage_count", 0))
                msg = f"无法删除：该分组包含 {usage} 台设备，请先移除设备"
            elif exc.code == "RESOURCE_GROUP_HAS_CHILDREN":
                child_count = int(exc.context.get("child_count", 0))
                msg = f"无法删除：该分组下还有 {child_count} 个子分组"
            elif exc.code == "RESOURCE_GROUP_NOT_FOUND":
                msg = "分组不存在"
            else:
                msg = f"删除失败: {exc.message}"
            return RedirectResponse(url=f"/groups?err={quote(msg)}", status_code=303)
        return RedirectResponse(url="/groups?msg=message.deleted", status_code=303)
    except Exception as exc:
        msg = f"操作失败: {str(exc)}"
        return RedirectResponse(url=f"/groups?err={quote(msg)}", status_code=303)


@router.get("/templates", summary="备份模板页面", description="查看设备备份模板列表")
def templates_page(request: Request, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["templates.view", "templates.create", "templates.update", "templates.delete"],
    )
    list_query = EditableListQueryInput.from_query_params(request.query_params)
    pagination_params = pagination_service.normalize_pagination_params(
        page=list_query.page,
        limit=list_query.limit,
        limit_in_query=list_query.include_limit_param,
    )

    total = crud.count_templates(session)
    items = crud.list_templates(session, limit=pagination_params.limit, offset=pagination_params.offset)
    current = None
    if list_query.edit and list_query.edit.isdigit():
        current = crud.get_template(session, int(list_query.edit))

    pagination = pagination_service.build_pagination_data(
        page=pagination_params.page,
        limit=pagination_params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/templates",
        params={},
        limit=pagination.limit,
        limit_explicit=pagination_params.limit_explicit,
    )

    default_platforms = PLATFORMS
    return templates.TemplateResponse(
        request=request,
        name="templates.html",
        context={
            **_layout_context(request=request, active="templates"),
            "items": items,
            "default_commands": DEFAULT_COMMANDS,
            "default_platforms": default_platforms,
            "current": current,
            "pagination": pagination.as_dict(),
            "pagination_base": pagination_base,
        },
    )


@router.post("/templates", summary="创建或更新模板", description="新增或修改备份模板规则")
def create_template(
    request: Request,
    session: Session = Depends(get_session),
    template_id: int = Form(0),
    name: str = Form(...),
    platform: str = Form(...),
    commands: str = Form(""),
):
    if template_id and int(template_id) > 0:
        _require_permission(request, "templates.update")
    else:
        _require_permission(request, "templates.create")
    try:
        if template_id and int(template_id) > 0:
            tpl = resource_service.update_template(
                session,
                int(template_id),
                resource_service.TemplateUpdateInput(
                    name=name,
                    platform=platform,
                    commands=commands,
                ),
            )
            _log_action(request, session, "UPDATE_TEMPLATE", "template", tpl.id, f"Name: {tpl.name}")
        else:
            tpl = resource_service.create_template(
                session,
                resource_service.TemplateCreateInput(
                    name=name,
                    platform=platform,
                    commands=commands,
                ),
            )
            _log_action(request, session, "CREATE_TEMPLATE", "template", tpl.id, f"Name: {tpl.name}")
    except resource_service.ServiceError as exc:
        return RedirectResponse(url=f"/templates?err={quote(exc.message)}", status_code=303)
    return RedirectResponse(url="/templates?msg=message.saved", status_code=303)


@router.post("/templates/{template_id}/delete", summary="删除模板", description="删除指定的备份模板")
def delete_template(request: Request, template_id: int, session: Session = Depends(get_session)):
    _require_permission(request, "templates.delete")
    try:
        tpl = resource_service.delete_template(session, template_id)
        _log_action(request, session, "DELETE_TEMPLATE", "template", template_id, f"Name: {tpl.name}")
    except resource_service.ServiceError as exc:
        return RedirectResponse(url=f"/templates?err={quote(exc.message)}", status_code=303)
    return RedirectResponse(url="/templates?msg=message.deleted", status_code=303)
