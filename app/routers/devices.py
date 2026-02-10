from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlmodel import select

from app import crud
from app.db import session_scope
from app.models import Device
from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id, platforms_compatible
from app.routers.common import _dt_local_str, _layout_context, _log_action, _require_admin, _require_operator, _current_user, get_user_allowed_group_ids, templates
from app.scheduler import plan_bulk_backup_run
from app.celery_tasks import bulk_reachability_task
from app.celery_app import celery_app


router = APIRouter()


@router.get("/devices")
def devices_page(request: Request):
    q = (request.query_params.get("q") or "").strip() or None
    login_method_raw = (request.query_params.get("login_method") or "").strip().lower()
    login_method = login_method_raw if login_method_raw in {"ssh", "telnet"} else None
    platform = (request.query_params.get("platform") or "").strip() or None
    if platform and login_method:
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, platform)
        else:
            platform = base_platform
    group_id_raw = (request.query_params.get("group_id") or "").strip()
    group_id = int(group_id_raw) if group_id_raw.isdigit() and int(group_id_raw) > 0 else None
    
    status_raw = (request.query_params.get("status") or "").strip().lower()
    reachability_status = None
    if status_raw == "online":
        reachability_status = True
    elif status_raw == "offline":
        reachability_status = False

    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    page_size = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if page_size > 100:
        page_size = 100
    msg = (request.query_params.get("msg") or "").strip()
    err = (request.query_params.get("err") or "").strip()
    offset = (page - 1) * page_size

    # Check permissions
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request))

    with session_scope() as session:
        total = crud.count_devices(
            session,
            q=q,
            login_method=login_method,
            platform=platform,
            group_id=group_id,
            reachability_status=reachability_status,
            allowed_group_ids=allowed_group_ids,
        )
        devices = crud.search_devices(
            session,
            q=q,
            login_method=login_method,
            platform=platform,
            group_id=group_id,
            reachability_status=reachability_status,
            limit=page_size,
            offset=offset,
            allowed_group_ids=allowed_group_ids,
        )
        tmpl = crud.list_templates(session)
        creds = crud.list_credentials(session)
        groups = crud.list_groups(session)
        group_map = {g.id: g for g in groups if g.id}
        cred_map = {c.id: c for c in creds if c.id}
    qs = []
    if q:
        qs.append(f"q={q}")
    if login_method:
        qs.append(f"login_method={login_method}")
    if platform:
        qs.append(f"platform={platform}")
    if group_id:
        qs.append(f"group_id={group_id}")
    if status_raw:
        qs.append(f"status={status_raw}")
    if page_size != 10:
        qs.append(f"limit={page_size}")
    base = "/devices"
    if qs:
        base = base + "?" + "&".join(qs)
    pagination_base = base + ("&" if "?" in base else "?") + "page="
    return templates.TemplateResponse(
        "devices.html",
        {
            **_layout_context(request=request, active="devices"),
            "devices": devices,
            "templates": tmpl,
            "credentials": creds,
            "groups": groups,
            "group_map": group_map,
            "credential_map": cred_map,
            "filters": {
                "q": q or "",
                "login_method": login_method or "",
                "platform": platform or "",
                "group_id": group_id or 0,
                "status": status_raw,
            },
            "pagination": {
                "page": page,
                "limit": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
            "pagination_base": pagination_base,
            "msg": msg,
            "err": err,
        },
    )


@router.post("/devices/bulk_backup")
def bulk_backup(
    request: Request,
    device_ids: str = Form(""),
    mode: str = Form("selected"),
):
    _require_operator(request)
    with session_scope() as session:
        if mode == "all":
            ids = [int(d.id) for d in crud.list_devices(session) if d.id]
        else:
            ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]

        if not ids:
            return RedirectResponse(url="/devices", status_code=303)

        existing = {int(did) for did in session.exec(select(Device.id).where(Device.id.in_(ids)))}
        filtered_ids = [did for did in ids if did in existing]
        if not filtered_ids:
            return RedirectResponse(url="/devices?err=未找到有效设备", status_code=303)
        run_id, jobs = plan_bulk_backup_run(session, filtered_ids, trigger="manual")

    if not jobs:
        return RedirectResponse(url="/devices?err=未找到有效设备", status_code=303)

    from app.celery_tasks import enqueue_schedule_run

    enqueued = enqueue_schedule_run(run_id=run_id, jobs=jobs)
    if not enqueued:
        return RedirectResponse(url="/devices?err=Celery 未启用或不可用", status_code=303)
    return RedirectResponse(url="/backups", status_code=303)



@router.post("/devices/bulk_delete")
def bulk_delete_devices(request: Request, device_ids: str = Form("")):
    _require_operator(request)
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    if not ids:
        return RedirectResponse(url="/devices", status_code=303)
    with session_scope() as session:
        for did in ids:
            d = crud.get_device(session, did)
            if d:
                name = d.name
                crud.delete_device(session, did, commit=False)
                _log_action(request, session, "DELETE_DEVICE", "device", did, f"Name: {name} (Bulk)")
        session.commit()
    return RedirectResponse(url="/devices?msg=设备已删除", status_code=303)


@router.get("/devices/export.csv")
def export_devices_csv(request: Request):
    q = (request.query_params.get("q") or "").strip() or None
    login_method_raw = (request.query_params.get("login_method") or "").strip().lower()
    login_method = login_method_raw if login_method_raw in {"ssh", "telnet"} else None
    platform = (request.query_params.get("platform") or "").strip() or None
    if platform and login_method:
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, platform)
        else:
            platform = base_platform
    group_id_raw = (request.query_params.get("group_id") or "").strip()
    group_id = int(group_id_raw) if group_id_raw.isdigit() and int(group_id_raw) > 0 else None
    
    status_raw = (request.query_params.get("status") or "").strip().lower()
    reachability_status = None
    if status_raw == "online":
        reachability_status = True
    elif status_raw == "offline":
        reachability_status = False

    # Check permissions
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request))

    with session_scope() as session:
        devices = crud.search_devices(
            session,
            q=q,
            login_method=login_method,
            platform=platform,
            group_id=group_id,
            reachability_status=reachability_status,
            limit=100000,
            offset=0,
            allowed_group_ids=allowed_group_ids,
        )
        groups = {g.id: g for g in crud.list_groups(session) if g.id}
        creds = {c.id: c for c in crud.list_credentials(session) if c.id}

    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=["name", "host", "port", "login_method", "platform", "group_name", "credential_name"],
        lineterminator="\n",
    )
    w.writeheader()
    for d in devices:
        group_name = groups.get(d.group_id).name if d.group_id and d.group_id in groups else ""
        cred_name = creds.get(d.credential_id).name if d.credential_id and d.credential_id in creds else ""
        w.writerow(
            {
                "name": d.name,
                "host": d.host,
                "port": d.port,
                "login_method": getattr(d, "login_method", "ssh") or "ssh",
                "platform": d.platform,
                "group_name": group_name,
                "credential_name": cred_name,
            }
        )
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="devices.csv"'},
    )


@router.post("/devices/import.csv")
async def import_devices_csv(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("insert"),
    match_by: str = Form("host"),
    download_report: int = Form(0),
):
    _require_admin(request)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return RedirectResponse(url="/devices?err=请上传CSV文件", status_code=303)
    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"name", "host", "port", "platform", "credential_name"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return RedirectResponse(url="/devices?err=CSV缺少必要列", status_code=303)

    created = 0
    updated = 0
    skipped = 0
    report: list[dict[str, str]] = []
    mode = (mode or "insert").strip()
    match_by = (match_by or "host").strip()
    if mode not in {"insert", "upsert"}:
        mode = "insert"
    if match_by not in {"host", "name"}:
        match_by = "host"
    with session_scope() as session:
        group_by_name: dict[str, int] = {}
        for g in crud.list_groups(session):
            if g.id:
                group_by_name[g.name] = g.id
        cred_by_name: dict[str, int] = {}
        for c in crud.list_credentials(session):
            if c.id:
                cred_by_name[c.name] = c.id

        for idx, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            host = (row.get("host") or "").strip()
            port_raw = (row.get("port") or "").strip()
            platform = (row.get("platform") or "").strip()
            login_method = (row.get("login_method") or "").strip().lower()
            group_name = (row.get("group_name") or "").strip()
            credential_name = (row.get("credential_name") or "").strip()

            if not name or not host or not port_raw.isdigit() or not platform or not credential_name:
                skipped += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": "字段缺失或端口非法",
                    }
                )
                continue
            if login_method not in {"ssh", "telnet"}:
                login_method = "telnet" if platform.endswith("_telnet") else "ssh"
            base_platform = normalize_platform_id(platform)
            if login_method == "telnet":
                if base_platform not in TELNET_DEVICE_TYPE_MAP:
                    skipped += 1
                    report.append(
                        {
                            "row": str(idx),
                            "action": "skip",
                            "name": name,
                            "host": host,
                            "message": "Telnet 不支持该平台类型",
                        }
                    )
                    continue
                platform = TELNET_DEVICE_TYPE_MAP[base_platform]
            else:
                platform = base_platform
            credential_id = cred_by_name.get(credential_name)
            if not credential_id:
                skipped += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": "未找到匹配的 credential_name",
                    }
                )
                continue
            group_id_val = None
            if group_name:
                gid = group_by_name.get(group_name)
                if not gid:
                    g = crud.create_group(session, name=group_name)
                    gid = g.id
                    if gid:
                        group_by_name[group_name] = gid
                group_id_val = gid

            existing = None
            if mode == "upsert":
                if match_by == "host":
                    existing = session.exec(select(Device).where(Device.host == host)).first()
                else:
                    existing = session.exec(select(Device).where(Device.name == name)).first()
            if existing:
                existing.name = name
                existing.host = host
                existing.port = int(port_raw)
                existing.platform = platform
                existing.login_method = login_method
                existing.group_id = group_id_val
                existing.credential_id = credential_id
                session.add(existing)
                updated += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "update",
                        "name": name,
                        "host": host,
                        "message": "已更新",
                    }
                )
            else:
                device = Device(
                    name=name,
                    host=host,
                    port=int(port_raw),
                    login_method=login_method,
                    platform=platform,
                    group_id=group_id_val,
                    credential_id=credential_id,
                )
                crud.create_device(session, device=device)
                created += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "create",
                        "name": name,
                        "host": host,
                        "message": "已创建",
                    }
                )

    if int(download_report or 0) == 1:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["row", "action", "name", "host", "message"], lineterminator="\n")
        w.writeheader()
        for r in report:
            w.writerow(r)
        content = "\ufeff" + buf.getvalue()
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="import_report.csv"'},
        )

    return templates.TemplateResponse(
        "import_result.html",
        {
            **_layout_context(request=request, active="devices"),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "mode": mode,
            "match_by": match_by,
            "report": report,
        },
    )


@router.post("/devices")
def create_device(
    request: Request,
    background: BackgroundTasks,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(22),
    login_method: str = Form("ssh"),
    platform: str = Form(...),
    group_id: int = Form(0),
    credential_id: int = Form(...),
    default_template_id: int = Form(0),
):
    _require_operator(request)
    with session_scope() as session:
        cred = crud.get_credential(session, int(credential_id))
        if cred is None:
            raise HTTPException(status_code=400, detail="Credential not found")
        login_method = (login_method or "ssh").strip().lower()
        if login_method not in {"ssh", "telnet"}:
            login_method = "ssh"
        platform = (platform or "").strip()
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            if base_platform not in TELNET_DEVICE_TYPE_MAP:
                raise HTTPException(status_code=400, detail="Telnet not supported for this platform")
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, base_platform + "_telnet")
        else:
            platform = base_platform
        dtid = int(default_template_id) if default_template_id else 0
        if dtid:
            tpl = crud.get_template(session, dtid)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, platform):
                raise HTTPException(status_code=400, detail="Template platform mismatch")
        device = Device(
            name=name.strip(),
            host=host.strip(),
            port=int(port),
            login_method=login_method,
            platform=platform,
            group_id=int(group_id) or None,
            credential_id=int(credential_id),
            default_template_id=dtid or None,
        )
        crud.create_device(session, device=device)
        new_device_id = device.id
        _log_action(request, session, "CREATE_DEVICE", "device", device.id, f"Name: {name}, Host: {host}")
    
    bulk_reachability_task.delay(device_ids=[new_device_id], offset_minutes=0)
    return RedirectResponse(url="/devices?msg=设备已创建", status_code=303)


@router.post("/devices/{device_id}/delete")
def delete_device(request: Request, device_id: int):
    user = _require_operator(request)
    allowed_ids = get_user_allowed_group_ids(user)

    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
             # If device not found, usually we redirect or 404. 
             # Existing code didn't handle None device explicitly in delete_device before calling crud.delete_device?
             # crud.delete_device handles None.
             # But here we need to check permission on the device object.
             # So we must get it.
             return RedirectResponse(url="/devices?err=设备不存在", status_code=303)
        
        # Check permission
        if allowed_ids is not None:
             gid = device.group_id if device.group_id else 0
             allowed_set = set(allowed_ids)
             is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
             if not is_allowed:
                 raise HTTPException(status_code=403, detail="You do not have permission to delete this device")

        name = device.name if device else f"ID: {device_id}"
        crud.delete_device(session, device_id)
        _log_action(request, session, "DELETE_DEVICE", "device", device_id, f"Name: {name}")
    return RedirectResponse(url="/devices?msg=设备已删除", status_code=303)


@router.get("/devices/{device_id}")
def device_detail(request: Request, device_id: int):
    user = _current_user(request)
    allowed_ids = get_user_allowed_group_ids(user)

    page_raw = (request.query_params.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    limit_raw = (request.query_params.get("limit") or "10").strip()
    page_size = int(limit_raw) if limit_raw.isdigit() and int(limit_raw) > 0 else 10
    if page_size > 200:
        page_size = 200
    offset = (page - 1) * page_size

    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if device is None:
            raise HTTPException(status_code=404)
            
        # Check if user has access to this device
        if allowed_ids is not None:
             gid = device.group_id if device.group_id else 0
             allowed_set = set(allowed_ids)
             is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
             if not is_allowed:
                  raise HTTPException(status_code=403, detail="You do not have permission to view this device")

        total_backups = crud.count_device_backups(session, device_id)
        backups = crud.list_device_backups(session, device_id, limit=page_size, offset=offset)
        all_tmpl = crud.list_templates(session)
        tmpl = [t for t in all_tmpl if platforms_compatible(t.platform, device.platform)]
        
        groups_list = crud.list_groups(session)
        if allowed_ids is not None:
             allowed_set = set(allowed_ids)
             groups_list = [g for g in groups_list if g.id in allowed_set]
        
        groups = {g.id: g for g in groups_list if g.id}
        creds = {c.id: c for c in crud.list_credentials(session) if c.id}

    # Base URL for pagination
    base = f"/devices/{device_id}"
    qs = []
    if page_size != 10:
        qs.append(f"limit={page_size}")
    if qs:
        base = base + "?" + "&".join(qs)
    pagination_base = base + ("&" if "?" in base else "?") + "page="

    return templates.TemplateResponse(
        "device_detail.html",
        {
            **_layout_context(request=request, active="devices"),
            "device": device,
            "backups": backups,
            "templates": tmpl,
            "groups": groups,
            "credentials": creds,
            "pagination": {
                "page": page,
                "limit": page_size,
                "total": total_backups,
                "total_pages": max(1, (total_backups + page_size - 1) // page_size),
            },
            "pagination_base": pagination_base,
        },
    )


@router.post("/devices/{device_id}/update")
def update_device(
    request: Request,
    device_id: int,
    background: BackgroundTasks,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(22),
    login_method: str = Form("ssh"),
    platform: str = Form(...),
    group_id: int = Form(0),
    credential_id: int = Form(...),
    default_template_id: int = Form(0),
):
    user = _require_operator(request)
    allowed_ids = get_user_allowed_group_ids(user)
    
    # Check if user can move to target group
    if allowed_ids is not None:
        target_gid = int(group_id) if group_id else 0
        allowed_set = set(allowed_ids)
        is_allowed = (target_gid in allowed_set) or (target_gid == 0 and (-1 in allowed_set or 0 in allowed_set))
        if not is_allowed:
             raise HTTPException(status_code=403, detail="You do not have permission to move devices to this group")

    with session_scope() as session:
        # Check if user owns the device
        current_device = crud.get_device(session, device_id)
        if not current_device:
            raise HTTPException(status_code=404, detail="Device not found")
            
        if allowed_ids is not None:
            gid = current_device.group_id if current_device.group_id else 0
            allowed_set = set(allowed_ids)
            is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
            if not is_allowed:
                 raise HTTPException(status_code=403, detail="You do not have permission to edit this device")

        if crud.get_credential(session, int(credential_id)) is None:
            raise HTTPException(status_code=400, detail="Credential not found")
        login_method = (login_method or "ssh").strip().lower()
        if login_method not in {"ssh", "telnet"}:
            login_method = "ssh"
        platform = (platform or "").strip()
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            if base_platform not in TELNET_DEVICE_TYPE_MAP:
                raise HTTPException(status_code=400, detail="Telnet not supported for this platform")
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, base_platform + "_telnet")
        else:
            platform = base_platform
        dtid = int(default_template_id) if default_template_id else 0
        if dtid:
            tpl = crud.get_template(session, dtid)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, platform):
                raise HTTPException(status_code=400, detail="Template platform mismatch")
        updated = crud.update_device(
            session,
            device_id,
            name=name,
            host=host,
            port=port,
            login_method=login_method,
            platform=platform,
            group_id=int(group_id) or None,
            credential_id=int(credential_id),
            default_template_id=dtid or None,
        )
        if updated is None:
            raise HTTPException(status_code=404)
        _log_action(request, session, "UPDATE_DEVICE", "device", device_id, f"Name: {name}, Host: {host}")
    
    bulk_reachability_task.delay(device_ids=[device_id], offset_minutes=0)
    return RedirectResponse(url=f"/devices/{device_id}?msg=修改已保存", status_code=303)


@router.post("/devices/{device_id}/backup")
def trigger_backup(request: Request, device_id: int, template_id: int = Form(0)):
    _require_operator(request)
    template_id = int(template_id) if template_id else 0
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if device is None:
            raise HTTPException(status_code=404)
        effective_template_id = template_id or int(getattr(device, "default_template_id", 0) or 0)
        if effective_template_id:
            tpl = crud.get_template(session, effective_template_id)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, device.platform):
                raise HTTPException(status_code=400, detail="Template platform mismatch")
        record = crud.create_backup_record(session, device_id=device_id, template_id=effective_template_id or None)
        record_id = record.id
        _log_action(request, session, "TRIGGER_BACKUP", "device", device_id, f"Backup Record ID: {record_id}")

    from app.celery_tasks import enqueue_backup_record

    enqueued = enqueue_backup_record(
        record_id=record_id,
        device_id=device_id,
        template_id=effective_template_id or None,
        skip_email=False,
    )
    if not enqueued:
        return RedirectResponse(url=f"/devices/{device_id}?err=Celery 未启用或不可用", status_code=303)
    return RedirectResponse(url=f"/devices/{device_id}?msg=备份任务已启动", status_code=303)


@router.post("/api/devices/{device_id}/backup")
def api_trigger_backup(request: Request, device_id: int, template_id: int = Form(0)):
    _require_operator(request)
    template_id = int(template_id) if template_id else 0
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if device is None:
            raise HTTPException(status_code=404)
        effective_template_id = template_id or int(getattr(device, "default_template_id", 0) or 0)
        if effective_template_id:
            tpl = crud.get_template(session, effective_template_id)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, device.platform):
                raise HTTPException(status_code=400, detail="Template platform mismatch")
        record = crud.create_backup_record(session, device_id=device_id, template_id=effective_template_id or None)
        record_id = record.id
        record_device_id = record.device_id
        record_started_at = record.started_at
        _log_action(request, session, "TRIGGER_BACKUP_API", "device", device_id, f"Backup Record ID: {record_id}")

    from app.celery_tasks import enqueue_backup_record

    enqueued = enqueue_backup_record(
        record_id=record_id,
        device_id=device_id,
        template_id=effective_template_id or None,
        skip_email=False,
    )
    if not enqueued:
        raise HTTPException(status_code=503, detail="Celery 未启用或不可用")
    return {
        "record": {
            "id": str(record_id),
            "device_id": int(record_device_id),
            "started_at": _dt_local_str(record_started_at, offset_minutes=offset_minutes),
        }
    }


@router.post("/api/devices/bulk_backup")
def api_bulk_backup(
    request: Request,
    device_ids: str = Form(""),
    mode: str = Form("selected"),
):
    _require_operator(request)
    with session_scope() as session:
        if mode == "all":
            ids = [int(d.id) for d in crud.list_devices(session) if d.id]
        else:
            ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]

        if not ids:
            return {"records": []}

        existing = {int(did) for did in session.exec(select(Device.id).where(Device.id.in_(ids)))}
        filtered_ids = [did for did in ids if did in existing]
        if not filtered_ids:
            return {"records": []}

        run_id, jobs = plan_bulk_backup_run(session, filtered_ids, trigger="manual")
        _log_action(request, session, "BULK_BACKUP_API", "device", None, f"Run ID: {run_id}, Jobs: {len(jobs)}")

    from app.celery_tasks import enqueue_schedule_run

    enqueued = enqueue_schedule_run(run_id=run_id, jobs=jobs)
    if not enqueued:
        raise HTTPException(status_code=503, detail="Celery 未启用或不可用")
    return {"records": [str(rid) for _, rid, __ in jobs]}


@router.post("/api/devices/bulk_reachability")
def api_bulk_reachability(
    request: Request,
    background: BackgroundTasks,
    device_ids: str = Form(""),
    q: str = Form(""),
    login_method: str = Form(""),
    platform: str = Form(""),
    group_id: int = Form(0),
    status: str = Form(""),
):
    _require_operator(request)
    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    q = (q or "").strip() or None
    login_method_raw = (login_method or "").strip().lower()
    login_method = login_method_raw if login_method_raw in {"ssh", "telnet"} else None
    platform = (platform or "").strip() or None
    if platform and login_method:
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, platform)
        else:
            platform = base_platform
    group_id_val = int(group_id) if int(group_id or 0) > 0 else None
    
    status_raw = (status or "").strip().lower()
    reachability_status = None
    if status_raw == "online":
        reachability_status = True
    elif status_raw == "offline":
        reachability_status = False

    # Check permissions
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request))

    # 如果没有指定 ID，则根据筛选条件查找
    if not ids and not device_ids:
        with session_scope() as session:
            devices = crud.search_devices(
                session,
                q=q,
                login_method=login_method,
                platform=platform,
                group_id=group_id_val,
                reachability_status=reachability_status,
                limit=100000,
                offset=0,
                allowed_group_ids=allowed_group_ids,
            )
            ids = [d.id for d in devices if d.id]
    
    # If IDs are provided (either from input or search), filter them by permissions if needed
    if ids and allowed_group_ids is not None:
        with session_scope() as session:
            # We need to verify these IDs are allowed
            devices_subset = crud.get_devices_subset(session, ids)
            allowed_set = set(allowed_group_ids)
            valid_ids = []
            for d in devices_subset:
                # Logic: allowed_group_ids contains real IDs and possibly -1/0 for ungrouped
                gid = d.group_id if d.group_id else 0
                is_allowed = False
                if gid in allowed_set:
                    is_allowed = True
                elif gid == 0 and (-1 in allowed_set or 0 in allowed_set):
                    is_allowed = True
                
                if is_allowed:
                    valid_ids.append(d.id)
            ids = valid_ids

    if not ids:
        return {"task_id": None}

    task = bulk_reachability_task.delay(device_ids=ids, offset_minutes=offset_minutes)
    return {"task_id": task.id}


@router.get("/api/devices/reachability_tasks/{task_id}")
def get_reachability_task_status(request: Request, task_id: str):
    _require_admin(request)
    result = AsyncResult(task_id, app=celery_app)
    if result.state == 'PENDING':
        return {
            "id": task_id,
            "status": "pending",
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "items": []
        }
    elif result.state == 'PROGRESS':
        meta = result.info or {}
        return {
            "id": task_id,
            "status": "running",
            "total": meta.get("total", 0),
            "processed": meta.get("processed", 0),
            "success": meta.get("success", 0),
            "failed": meta.get("failed", 0),
            "items": meta.get("items", []),
        }
    elif result.state == 'SUCCESS':
        res = result.result or {}
        return {
            "id": task_id,
            "status": "finished",
            "total": res.get("total", 0),
            "processed": res.get("processed", 0),
            "success": res.get("success", 0),
            "failed": res.get("failed", 0),
            "items": res.get("items", []),
        }
    elif result.state == 'FAILURE':
        return {
            "id": task_id,
            "status": "failed",
            "error": str(result.result),
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "items": []
        }
    else:
        return {
            "id": task_id,
            "status": "running",
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "items": []
        }


@router.get("/api/devices/status")
def get_devices_status(request: Request, ids: str = ""):
    _require_admin(request)
    id_list = [int(x) for x in (ids or "").split(",") if x.strip().isdigit()]
    if not id_list:
        return {"items": []}

    offset_minutes = int(getattr(request.state, "tz_offset_minutes", 0))

    with session_scope() as session:
        devices = session.exec(select(Device).where(Device.id.in_(id_list))).all()
        results = []
        for d in devices:
            results.append({
                "id": d.id,
                "success": d.reachability_status,
                "last_checked": _dt_local_str(d.last_reachability_check, offset_minutes=offset_minutes) if d.last_reachability_check else None,
                "error_message": d.reachability_error,
                "duration_ms": d.reachability_duration_ms
            })
    return {"items": results}
