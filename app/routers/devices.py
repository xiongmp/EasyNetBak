from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import asyncssh
import telnetlib3
from celery.result import AsyncResult
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi_csrf_protect import CsrfProtect
from starlette.concurrency import run_in_threadpool
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.db import session_scope
from app.models import Device
from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id, platforms_compatible
from app.routers.common import _dt_local_str, _layout_context, _log_action, _require_permission, _current_user, get_user_allowed_group_ids, templates
from app.scheduler import plan_bulk_backup_run
from app.celery_tasks import bulk_reachability_task
from app.celery_app import celery_app
from app.services.auth import create_webshell_token, decode_session_token, decode_webshell_token


router = APIRouter()


_SSH_ALGO_ERROR_MARKERS = (
    "no matching encryption algorithm",
    "no matching cipher",
    "no common algorithms",
    "algorithm negotiation failed",
    "no matching key exchange",
    "no matching mac",
)

_SSH_LEGACY_CONNECT_ALGS: dict[str, list[str]] = {
    "encryption_algs": [
        "aes128-cbc",
        "aes192-cbc",
        "aes256-cbc",
        "3des-cbc",
    ],
    "kex_algs": [
        "diffie-hellman-group1-sha1",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group-exchange-sha1",
    ],
    "mac_algs": [
        "hmac-sha1",
        "hmac-sha1-96",
        "hmac-md5",
    ],
}


async def _connect_ssh_with_legacy_fallback(
    *,
    host: str,
    port: int,
    username: str,
    password: str | None,
) -> tuple[asyncssh.SSHClientConnection, bool]:
    base_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "known_hosts": None,
    }
    try:
        conn = await asyncssh.connect(**base_kwargs)
        return conn, False
    except Exception as first_exc:
        msg = str(first_exc).lower()
        if not any(marker in msg for marker in _SSH_ALGO_ERROR_MARKERS):
            raise
        legacy_kwargs = {**base_kwargs, **_SSH_LEGACY_CONNECT_ALGS}
        conn = await asyncssh.connect(**legacy_kwargs)
        return conn, True


def _load_webshell_context(user_id: int, device_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        user = crud.get_user(session, user_id)
        if not user:
            return None
        perms = crud.get_effective_permission_codes(user)
        if "devices.webshell" not in perms or "devices.view" not in perms:
            return None
        device = crud.get_device(session, device_id)
        if not device:
            return None
        allowed_ids = get_user_allowed_group_ids(user)
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
            if not is_allowed:
                return None
        secrets = crud.get_device_secrets(session, device)
        return {
            "device_id": int(device.id),
            "name": device.name or "",
            "host": device.host or "",
            "port": int(device.port or 0),
            "login_method": (device.login_method or "ssh").strip().lower(),
            "platform": device.platform or "",
            "username": secrets.get("username"),
            "password": secrets.get("password"),
            "enable_password": secrets.get("enable_password"),
        }


@router.get("/devices/{device_id}/webshell")
def device_webshell_page(
    request: Request,
    device_id: int,
    csrf_protect: CsrfProtect = Depends(),
):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.webshell")
    token = (request.query_params.get("token") or "").strip()
    payload = decode_webshell_token(token)
    user = _current_user(request)
    if not payload or not user:
        raise HTTPException(status_code=403, detail="Invalid token")
    if int(payload.get("uid", 0)) != int(user.id) or int(payload.get("did", 0)) != int(device_id):
        raise HTTPException(status_code=403, detail="Invalid token")
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # Check permission
        allowed_ids = get_user_allowed_group_ids(_current_user(request))
        if allowed_ids is not None:
             gid = device.group_id if device.group_id else 0
             allowed_set = set(allowed_ids)
             is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
             if not is_allowed:
                 raise HTTPException(status_code=403, detail="Permission denied")

        # Fetch credential
        credential = crud.get_credential(session, device.credential_id) if device.credential_id else None

        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        response = templates.TemplateResponse(
            request=request,
            name="webshell.html",
            context={
                "request": request,
                "device": device,
                "credential": credential,
                "webshell_token": token,
                "csrf_token": csrf_token,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, response)
        return response


@router.post("/devices/{device_id}/webshell")
def device_webshell_open(
    request: Request,
    device_id: int,
    csrf_protect: CsrfProtect = Depends(),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "devices.view")
    user = _require_permission(request, "devices.webshell")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            return RedirectResponse(url="/devices?err=设备不存在", status_code=303)
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
            if not is_allowed:
                raise HTTPException(status_code=403, detail="Permission denied")
        _log_action(request, session, "OPEN_WEBSHELL", "device", device_id, f"Name: {device.name}")
    token = create_webshell_token(user_id=int(user.id), device_id=int(device_id), ttl_seconds=60)
    return RedirectResponse(url=f"/devices/{device_id}/webshell?token={token}", status_code=303)


@router.post("/devices/{device_id}/webshell/token")
def device_webshell_token(
    request: Request,
    device_id: int,
    csrf_protect: CsrfProtect = Depends(),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "devices.view")
    user = _require_permission(request, "devices.webshell")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
            if not is_allowed:
                raise HTTPException(status_code=403, detail="Permission denied")
    token = create_webshell_token(user_id=int(user.id), device_id=int(device_id), ttl_seconds=60)
    return JSONResponse({"token": token})


@router.websocket("/ws/devices/{device_id}/shell")
async def device_webshell(websocket: WebSocket, device_id: int):
    token = websocket.cookies.get(settings.auth_cookie_name, "")
    payload = decode_session_token(token)
    user_id = int(payload.get("uid", 0)) if payload else 0
    if user_id <= 0:
        await websocket.close(code=4401)
        return
    ws_token = (websocket.query_params.get("token") or "").strip()
    ws_payload = decode_webshell_token(ws_token)
    if not ws_payload:
        await websocket.close(code=4403)
        return
    if int(ws_payload.get("uid", 0)) != int(user_id) or int(ws_payload.get("did", 0)) != int(device_id):
        await websocket.close(code=4403)
        return
    context = await run_in_threadpool(_load_webshell_context, user_id, device_id)
    if not context:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    started_at = datetime.utcnow()
    with session_scope() as session:
        try:
            user = crud.get_user(session, user_id)
            ip = getattr(websocket.client, "host", None)
            crud.create_audit_log(
                session,
                user_id=int(user.id) if user and user.id else None,
                username=user.username if user else None,
                action="OPEN_WEBSHELL",
                resource_type="device",
                resource_id=str(device_id),
                details=f"Host: {context.get('host')}:{context.get('port')} ({context.get('login_method')})",
                ip_address=ip,
            )
        except Exception:
            pass

    async def send_status(message: str):
        await websocket.send_text(json.dumps({"type": "status", "message": message}))

    async def send_error(message: str):
        await websocket.send_text(json.dumps({"type": "error", "message": message}))

    username = context.get("username")
    if not username:
        await send_error("未配置凭据")
        await websocket.close()
        return

    login_method = context.get("login_method") or "ssh"
    reader = None
    writer = None
    process = None
    conn = None

    try:
        await send_status(f"连接 {context.get('host')}:{context.get('port')}...")
        if login_method == "telnet":
            reader, writer = await telnetlib3.open_connection(
                host=context.get("host"),
                port=int(context.get("port") or 23),
                encoding="utf-8",
            )
        else:
            conn, legacy_mode = await _connect_ssh_with_legacy_fallback(
                host=context.get("host"),
                port=int(context.get("port") or 22),
                username=username,
                password=context.get("password") or None,
            )
            if legacy_mode:
                await send_status("已启用旧算法兼容模式")
            process = await conn.create_process(
                term_type="xterm",
                term_size=(120, 30),
                encoding="utf-8",
            )
            reader = process.stdout
            writer = process.stdin
        await send_status("连接成功")
    except Exception as exc:
        await send_error(f"连接失败: {str(exc)}")
        await websocket.close()
        return

    telnet_login_state = {"step": 0, "buffer": ""} # 0: user, 1: pass, 2: done

    async def pump_output(stream):
        while True:
            data = await stream.read(1024)
            if not data:
                break
            await websocket.send_text(json.dumps({"type": "output", "data": data}))

            # Auto-login for Telnet
            if login_method == "telnet" and stream == reader and telnet_login_state["step"] < 2:
                try:
                    text = data
                    if not isinstance(text, str):
                        text = text.decode("utf-8", errors="ignore")
                    
                    telnet_login_state["buffer"] += text
                    if len(telnet_login_state["buffer"]) > 500:
                         telnet_login_state["buffer"] = telnet_login_state["buffer"][-500:]
                    
                    buf_lower = telnet_login_state["buffer"].lower()
                    
                    if telnet_login_state["step"] == 0:
                        # Check for password first in case we skipped user
                        if "password:" in buf_lower:
                             writer.write((context.get("password") or "") + "\r\n")
                             telnet_login_state["step"] = 2
                             telnet_login_state["buffer"] = ""
                        elif any(p in buf_lower for p in ["login:", "username:", "user:", "name:"]):
                             writer.write((username or "") + "\r\n")
                             telnet_login_state["step"] = 1
                             telnet_login_state["buffer"] = ""
                             
                    elif telnet_login_state["step"] == 1:
                        if "password:" in buf_lower:
                             writer.write((context.get("password") or "") + "\r\n")
                             telnet_login_state["step"] = 2
                             telnet_login_state["buffer"] = ""
                except Exception:
                    pass

    async def ws_to_device():
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            msg_type = payload.get("type")
            if msg_type == "input":
                if writer:
                    writer.write(payload.get("data") or "")
            elif msg_type == "resize" and process:
                cols = int(payload.get("cols") or 120)
                rows = int(payload.get("rows") or 30)
                process.change_terminal_size(cols, rows)

    output_tasks = []
    try:
        if reader:
            output_tasks.append(asyncio.create_task(pump_output(reader)))
        if process and process.stderr:
            output_tasks.append(asyncio.create_task(pump_output(process.stderr)))
        input_task = asyncio.create_task(ws_to_device())
        done, pending = await asyncio.wait(
            output_tasks + [input_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            try:
                await task
            except WebSocketDisconnect:
                pass
            except asyncio.CancelledError:
                pass
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        for task in output_tasks:
            task.cancel()
        if process:
            process.kill()
        if conn:
            conn.close()
            await conn.wait_closed()
        if writer:
            try:
                writer.close()
            except Exception:
                pass
        try:
            duration = ""
            if started_at:
                delta = datetime.utcnow() - started_at
                duration = f"{int(delta.total_seconds())}s"
            with session_scope() as session:
                user = crud.get_user(session, user_id)
                ip = getattr(websocket.client, "host", None)
                crud.create_audit_log(
                    session,
                    user_id=int(user.id) if user and user.id else None,
                    username=user.username if user else None,
                    action="CLOSE_WEBSHELL",
                    resource_type="device",
                    resource_id=str(device_id),
                    details=f"Duration: {duration}",
                    ip_address=ip,
                )
        except Exception:
            pass
def _get_redirect_url(request: Request, base_url: str = "/devices", msg: str = None, err: str = None) -> str:
    params = dict(request.query_params)
    if "msg" in params:
        del params["msg"]
    if "err" in params:
        del params["err"]
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    
    if not params:
        return base_url
    qs = urlencode(params)
    return f"{base_url}?{qs}" if qs else base_url


@router.get("/devices")
def devices_page(request: Request, csrf_protect: CsrfProtect = Depends()):
    _require_permission(request, "devices.view")
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
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="devices.html",
        context={
            **_layout_context(request=request, active="devices"),
            "devices": devices,
            "templates": tmpl,
            "credentials": creds,
            "groups": groups,
            "group_map": group_map,
            "credential_map": cred_map,
            "csrf_token": csrf_token,
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
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/devices/bulk_backup")
def bulk_backup(
    request: Request,
    device_ids: str = Form(""),
    mode: str = Form("selected"),
):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
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
    _require_permission(request, "devices.delete")
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    if not ids:
        return RedirectResponse(url=_get_redirect_url(request, "/devices"), status_code=303)
    with session_scope() as session:
        for did in ids:
            d = crud.get_device(session, did)
            if d:
                name = d.name
                crud.delete_device(session, did, commit=False)
                _log_action(request, session, "DELETE_DEVICE", "device", did, f"Name: {name} (Bulk)")
        session.commit()
    return RedirectResponse(url=_get_redirect_url(request, "/devices", msg="设备已删除"), status_code=303)


@router.post("/devices/bulk_update")
def bulk_update_devices(
    request: Request,
    device_ids: str = Form(""),
    field: str = Form(""),
    value: str = Form(""),
):
    _require_permission(request, "devices.update")
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    if not ids:
        return RedirectResponse(url=_get_redirect_url(request, "/devices", err="未选择设备"), status_code=303)

    valid_fields = {"group_id", "platform", "login_method", "credential_id"}
    if field not in valid_fields:
        return RedirectResponse(url=_get_redirect_url(request, "/devices", err="无效的修改字段"), status_code=303)

    # Validate value
    new_value: Any = value
    if field == "group_id":
        try:
            gid = int(value)
            new_value = gid if gid > 0 else None
        except ValueError:
            new_value = None
    elif field == "credential_id":
        try:
            cid = int(value)
            new_value = cid
        except ValueError:
             return RedirectResponse(url=_get_redirect_url(request, "/devices", err="无效的凭据ID"), status_code=303)
    elif field == "login_method":
        if value not in ("ssh", "telnet"):
             return RedirectResponse(url=_get_redirect_url(request, "/devices", err="无效的登录方式"), status_code=303)
    elif field == "platform":
        if not value:
             return RedirectResponse(url=_get_redirect_url(request, "/devices", err="平台类型不能为空"), status_code=303)

    count = 0
    updated_ids = []
    with session_scope() as session:
        user = _current_user(request)
        allowed_ids = get_user_allowed_group_ids(user)
        
        # Verify credential exists if we are updating it
        if field == "credential_id":
             if not crud.get_credential(session, new_value):
                 return RedirectResponse(url=_get_redirect_url(request, "/devices", err="指定的凭据不存在"), status_code=303)

        for did in ids:
            d = crud.get_device(session, did)
            if not d:
                continue
            
            # Check permissions
            if allowed_ids is not None:
                gid = d.group_id if d.group_id else 0
                allowed_set = set(allowed_ids)
                is_allowed = (gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))
                if not is_allowed:
                    continue

            old_val = getattr(d, field)
            target_val = new_value
            
            updated = False
            if field == "group_id":
                if old_val != target_val:
                    d.group_id = target_val
                    _log_action(request, session, "UPDATE_DEVICE", "device", did, f"Bulk Update {field}: {old_val} -> {new_value}")
                    updated = True
            elif field == "platform":
                if old_val != target_val:
                    d.platform = target_val
                    _log_action(request, session, "UPDATE_DEVICE", "device", did, f"Bulk Update {field}: {old_val} -> {new_value}")
                    updated = True
            elif field == "login_method":
                 if old_val != target_val:
                    d.login_method = target_val
                    msg_suffix = ""
                    # Auto update port if standard
                    if target_val == "telnet" and d.port == 22:
                        d.port = 23
                        msg_suffix = " (Port: 22 -> 23)"
                    elif target_val == "ssh" and d.port == 23:
                        d.port = 22
                        msg_suffix = " (Port: 23 -> 22)"
                    
                    _log_action(request, session, "UPDATE_DEVICE", "device", did, f"Bulk Update {field}: {old_val} -> {new_value}{msg_suffix}")
                    updated = True
            elif field == "credential_id":
                 if old_val != target_val:
                    d.credential_id = target_val
                    _log_action(request, session, "UPDATE_DEVICE", "device", did, f"Bulk Update {field}: {old_val} -> {new_value}")
                    updated = True
            
            if updated:
                count += 1
                updated_ids.append(did)
        
        session.commit()

    if updated_ids:
        bulk_reachability_task.delay(device_ids=updated_ids, offset_minutes=0)

    return RedirectResponse(url=_get_redirect_url(request, "/devices", msg=f"成功更新 {count} 台设备"), status_code=303)


@router.get("/devices/import_template.csv")
def download_import_template(request: Request):
    _require_permission(request, "devices.create")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "host", "port", "login_method", "platform", "group_name", "credential_name"])
    w.writerow(["Example-Switch", "192.168.1.1", "22", "ssh", "cisco_ios", "Core", "default_cred"])
    
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="device_import_template.csv"'},
    )


@router.get("/devices/export.csv")
def export_devices_csv(request: Request):
    _require_permission(request, "devices.view")
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
):
    mode = (mode or "insert").strip()
    if mode not in {"insert", "upsert"}:
        mode = "insert"
    if mode == "upsert":
        _require_permission(request, "devices.update")
    else:
        _require_permission(request, "devices.create")
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return RedirectResponse(url="/devices?err=请上传CSV文件", status_code=303)
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
    required = {"name", "host", "port", "platform", "credential_name"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return RedirectResponse(url="/devices?err=CSV缺少必要列", status_code=303)

    created = 0
    updated = 0
    skipped = 0
    report: list[dict[str, str]] = []
    match_by = (match_by or "host").strip()
    if match_by not in {"host", "name"}:
        match_by = "host"
    
    affected_device_ids = []

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
                affected_device_ids.append(existing.id)
                _log_action(request, session, "UPDATE_DEVICE", "device", existing.id, f"Name: {name}, Host: {host} (Import)")
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
                session.flush()
                if device.id:
                    affected_device_ids.append(device.id)
                    _log_action(request, session, "CREATE_DEVICE", "device", device.id, f"Name: {name}, Host: {host} (Import)")
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

    if affected_device_ids:
        bulk_reachability_task.delay(device_ids=affected_device_ids, offset_minutes=0)

    return templates.TemplateResponse(
        request=request,
        name="import_result.html",
        context={
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
    _require_permission(request, "devices.create")
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
    user = _require_permission(request, "devices.delete")
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
    return RedirectResponse(url=_get_redirect_url(request, "/devices", msg="设备已删除"), status_code=303)


@router.get("/devices/{device_id}")
def device_detail(request: Request, device_id: int):
    _require_permission(request, "devices.view")
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
        request=request,
        name="device_detail.html",
        context={
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
    user = _require_permission(request, "devices.update")
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
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
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
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
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
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
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
    _require_permission(request, "devices.update")
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
    _require_permission(request, "devices.update")
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
    _require_permission(request, "devices.view")
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
