from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import asyncssh
import telnetlib3
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi_csrf_protect import CsrfProtect
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app import crud
from app.core.settings import settings
from app.db import get_session, session_scope
from app.models import WebshellRecord
from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id
from app.routers.support import (
    _current_user,
    _log_action,
    _require_any_permission,
    _require_permission,
    get_remote_ip,
    get_user_allowed_group_ids,
    has_permission,
)
from app.routers.web_context import _layout_context, templates
from app.schemas.inputs import BaseListQueryInput, DeviceListQueryInput
from app.celery_tasks import bulk_reachability_task
from app.services.auth import create_webshell_token, decode_session_token, decode_webshell_token
from app.services import backup_service, device_service, identity_service


router = APIRouter(tags=["设备管理 (Devices)"])


_SUPPORTED_DEVICE_ENCODINGS = {"utf-8", "gb18030", "gbk", "gb2312"}


def _normalize_device_encoding(value: str | None) -> str:
    encoding = (value or "utf-8").strip().lower()
    if not encoding:
        return "utf-8"
    return encoding if encoding in _SUPPORTED_DEVICE_ENCODINGS else "utf-8"


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
        user = identity_service.get_user(session, user_id)
        if not user:
            return None
        perms = identity_service.get_effective_permission_codes(user)
        if "devices.webshell" not in perms or "devices.view" not in perms:
            return None
        allowed_ids = get_user_allowed_group_ids(user, session=session)
        try:
            device = device_service.get_device_detail(
                session,
                device_id=device_id,
                allowed_group_ids=allowed_ids,
            )
        except device_service.ServiceError:
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


@router.get("/devices/{device_id}/webshell", summary="WebShell 页面", description="打开指定设备的 WebShell 终端页面")
def device_webshell_page(
    request: Request,
    device_id: int,
    session: Session = Depends(get_session),
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
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        payload = device_service.get_webshell_page_payload(
            session,
            device_id=device_id,
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="webshell.html",
        context={
            **_layout_context(request=request, active="devices"),
            **payload,
            "webshell_token": token,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/devices/{device_id}/webshell", summary="WebShell 登录", description="提交设备凭据以启动 WebShell 会话")
def device_webshell_open(
    request: Request,
    device_id: int,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends(),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "devices.view")
    user = _require_permission(request, "devices.webshell")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        device = device_service.get_device_detail(
            session,
            device_id=device_id,
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        if exc.status_code == 404:
            return RedirectResponse(url="/devices?err=设备不存在", status_code=303)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _log_action(request, session, "OPEN_WEBSHELL", "device", device_id, f"Name: {device.name}")
    token = create_webshell_token(user_id=int(user.id), device_id=int(device_id), ttl_seconds=60)
    return RedirectResponse(url=f"/devices/{device_id}/webshell?token={token}", status_code=303)


@router.post("/devices/{device_id}/webshell/token", summary="获取WebShell Token", description="生成用于连接WebShell的认证Token")
def device_webshell_token(
    request: Request,
    device_id: int,
    session: Session = Depends(get_session),
    csrf_protect: CsrfProtect = Depends(),
):
    csrf_protect.validate_csrf(request)
    _require_permission(request, "devices.view")
    user = _require_permission(request, "devices.webshell")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        device_service.get_device_detail(
            session,
            device_id=device_id,
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
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

    started_at = None
    start_ts = None
    record_filepath = None
    record_id = None

    def _append_record(data: str, is_output: bool = True):
        if not start_ts or not record_filepath:
            return
        try:
            offset = round(time.time() - start_ts, 4)
            event = [offset, "o" if is_output else "i", data]
            with open(record_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass

    async def send_status(message: str):
        await websocket.send_text(json.dumps({"type": "status", "message": message}))

    async def send_error(message: str):
        await websocket.send_text(json.dumps({"type": "error", "message": message}))

    try:
        init_msg_raw = await websocket.receive_text()
        init_payload = json.loads(init_msg_raw)
        if init_payload.get("type") != "init":
            await send_error("无效的初始化请求")
            await websocket.close()
            return
        
        login_type = init_payload.get("loginType", "auto")
        if login_type == "manual":
            context["username"] = init_payload.get("username")
            context["password"] = init_payload.get("password")
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
        return

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
                errors="replace",
            )
            reader = process.stdout
            writer = process.stdin
        await send_status("连接成功")

        started_at = datetime.utcnow()
        start_ts = time.time()
        os.makedirs("data/recordings", exist_ok=True)
        record_filename = f"{uuid4().hex}.cast"
        record_filepath = os.path.join("data/recordings", record_filename)
        header = {
            "version": 2,
            "width": 120,
            "height": 30,
            "timestamp": int(start_ts),
            "env": {"TERM": "xterm-256color"}
        }
        try:
            with open(record_filepath, "w", encoding="utf-8") as f:
                f.write(json.dumps(header) + "\n")
        except Exception:
            pass

        with session_scope() as session:
            try:
                user = crud.get_user(session, user_id)
                ip = getattr(websocket.client, "host", None)
                record = WebshellRecord(
                    user_id=int(user.id) if user and user.id else None,
                    username=user.username if user else "unknown",
                    device_id=int(device_id),
                    device_name=context.get("name") or "unknown",
                    device_host=context.get("host") or "unknown",
                    device_login_name=context.get("username"),
                    started_at=started_at,
                    file_path=record_filepath
                )
                session.add(record)
                session.flush()
                record_id = record.id

                crud.create_audit_log(
                    session,
                    user_id=int(user.id) if user and user.id else None,
                    username=user.username if user else None,
                    action="OPEN_WEBSHELL",
                    resource_type="device",
                    resource_id=str(device_id),
                    details=f"Host: {context.get('host')}:{context.get('port')} ({context.get('login_method')}), Record ID: {record_id}",
                    ip_address=ip,
                )
            except Exception:
                pass

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
            try:
                _append_record(data if isinstance(data, str) else data.decode("utf-8", "ignore"), True)
            except Exception:
                pass
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
            total_sec = 0
            if started_at:
                delta = datetime.utcnow() - started_at
                total_sec = int(delta.total_seconds())
                duration = f"{total_sec}s"
            with session_scope() as session:
                user = crud.get_user(session, user_id)
                ip = get_remote_ip(websocket)
                
                if record_id:
                    rec = session.get(WebshellRecord, record_id)
                    if rec:
                        rec.duration = total_sec
                        session.add(rec)

                crud.create_audit_log(
                    session,
                    user_id=int(user.id) if user and user.id else None,
                    username=user.username if user else None,
                    action="CLOSE_WEBSHELL",
                    resource_type="device",
                    resource_id=str(device_id),
                    details=f"Duration: {duration}, Record ID: {record_id}",
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


@router.get("/devices", summary="设备列表页面", description="展示网络设备列表，支持搜索、筛选和分页")
def devices_page(request: Request, csrf_protect: CsrfProtect = Depends(), session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["devices.view", "devices.create", "devices.update", "devices.delete"],
    )
    msg = (request.query_params.get("msg") or "").strip()
    err = (request.query_params.get("err") or "").strip()
    list_query = DeviceListQueryInput.from_query_params(request.query_params)
    filters = device_service.normalize_list_filters(
        q=list_query.q,
        login_method=list_query.login_method,
        platform=list_query.platform,
        group_id=list_query.group_id,
        status=list_query.status,
    )
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)

    payload = device_service.get_devices_page_payload(
        session,
        filters=filters,
        page=list_query.page,
        page_size=list_query.limit,
        include_limit_param=list_query.include_limit_param,
        allowed_group_ids=allowed_group_ids,
    )
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request=request,
        name="devices.html",
        context={
            **_layout_context(request=request, active="devices"),
            "csrf_token": csrf_token,
            **payload,
            "msg": msg,
            "err": err,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.post("/devices/bulk_backup", summary="批量触发备份", description="异步触发多个设备的备份任务")
def bulk_backup(
    request: Request,
    session: Session = Depends(get_session),
    device_ids: str = Form(""),
    mode: str = Form("selected"),
):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
    requested_ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    result = backup_service.trigger_bulk_backup(
        session,
        requested_ids=requested_ids,
        mode=mode,
            allowed_group_ids=get_user_allowed_group_ids(_current_user(request), session=session),
    )

    if not result.requested_ids:
        return RedirectResponse(url="/devices", status_code=303)
    if not result.jobs:
        return RedirectResponse(url="/devices?err=未找到有效设备", status_code=303)
    if result.enqueue_status == "none":
        return RedirectResponse(url="/devices?err=Celery 未启用或不可用", status_code=303)
    if result.enqueue_status == "partial":
        started = len(result.enqueued_record_ids)
        return RedirectResponse(
            url=f"/devices?msg=已启动 {started} 个备份任务，部分任务入队失败",
            status_code=303,
        )
    return RedirectResponse(url="/backups", status_code=303)



@router.post("/devices/bulk_delete", summary="批量删除设备", description="批量删除选中的网络设备")
def bulk_delete_devices(request: Request, session: Session = Depends(get_session), device_ids: str = Form("")):
    user = _require_permission(request, "devices.delete")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    if not ids:
        return RedirectResponse(url=_get_redirect_url(request, "/devices"), status_code=303)
    try:
        deleted = device_service.bulk_delete_devices(session, device_ids=ids, allowed_group_ids=allowed_ids)
    except device_service.ServiceError as exc:
        if exc.code == "DEVICE_BULK_DELETE_ACTIVE_BACKUPS":
            return RedirectResponse(url=_get_redirect_url(request, "/devices", err="存在执行中的备份任务，无法删除设备"), status_code=303)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    for item in deleted:
        _log_action(
            request,
            session,
            "DELETE_DEVICE",
            "device",
            item["device_id"],
            f"Name: {item['name']} (Bulk)",
        )
    return RedirectResponse(url=_get_redirect_url(request, "/devices", msg="设备已删除"), status_code=303)


@router.post("/devices/bulk_update", summary="批量更新设备", description="批量更新多个设备的基本属性或分组")
def bulk_update_devices(
    request: Request,
    session: Session = Depends(get_session),
    device_ids: str = Form(""),
    field: str = Form(""),
    value: str = Form(""),
):
    _require_permission(request, "devices.update")
    ids = [int(x) for x in (device_ids or "").split(",") if x.strip().isdigit()]
    if not ids:
        return RedirectResponse(url=_get_redirect_url(request, "/devices", err="未选择设备"), status_code=303)
    try:
        result = device_service.bulk_update_devices(
            session,
            device_ids=ids,
            field=field,
            value=value,
            allowed_group_ids=get_user_allowed_group_ids(_current_user(request), session=session),
        )
    except device_service.ServiceError as exc:
        return RedirectResponse(url=_get_redirect_url(request, "/devices", err=exc.message), status_code=303)

    for entry in result["log_entries"]:
        _log_action(request, session, "UPDATE_DEVICE", "device", entry["device_id"], entry["message"])

    updated_ids = result["updated_ids"]
    if updated_ids:
        bulk_reachability_task.delay(device_ids=updated_ids, offset_minutes=0)

    return RedirectResponse(
        url=_get_redirect_url(request, "/devices", msg=f"成功更新 {result['count']} 台设备"),
        status_code=303,
    )


@router.get("/devices/import_template.csv", summary="下载设备导入模板", description="获取设备导入的CSV模板文件")
def download_import_template(request: Request):
    _require_permission(request, "devices.create")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "name",
            "host",
            "port",
            "login_method",
            "platform",
            "encoding",
            "group_name",
            "credential_name",
            "default_template_name",
        ]
    )
    w.writerow(
        [
            "Example-Switch",
            "192.168.1.1",
            "22",
            "ssh",
            "cisco_ios",
            "utf-8",
            "Core",
            "default_cred",
            "Cisco-Config-Backup",
        ]
    )
    
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="device_import_template.csv"'},
    )


@router.get("/devices/export.csv", summary="导出设备列表", description="将设备列表导出为CSV文件")
def export_devices_csv(request: Request, session: Session = Depends(get_session)):
    _require_permission(request, "devices.view")
    list_query = DeviceListQueryInput.from_query_params(request.query_params)
    filters = device_service.normalize_list_filters(
        q=list_query.q,
        login_method=list_query.login_method,
        platform=list_query.platform,
        group_id=list_query.group_id,
        status=list_query.status,
    )
    allowed_group_ids = get_user_allowed_group_ids(_current_user(request), session=session)

    payload = device_service.get_devices_export_payload(
        session,
        filters=filters,
        allowed_group_ids=allowed_group_ids,
    )

    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=[
            "name",
            "host",
            "port",
            "login_method",
            "platform",
            "encoding",
            "group_name",
            "credential_name",
            "default_template_name",
        ],
        lineterminator="\n",
    )
    w.writeheader()
    for d in payload["devices"]:
        groups = payload["groups"]
        credentials = payload["credentials"]
        templates_map = payload["templates"]
        group_name = groups.get(d.group_id).name if d.group_id and d.group_id in groups else ""
        cred_name = credentials.get(d.credential_id).name if d.credential_id and d.credential_id in credentials else ""
        default_template_name = (
            templates_map.get(d.default_template_id).name
            if d.default_template_id and d.default_template_id in templates_map
            else ""
        )
        w.writerow(
            {
                "name": d.name,
                "host": d.host,
                "port": d.port,
                "login_method": getattr(d, "login_method", "ssh") or "ssh",
                "platform": d.platform,
                "encoding": getattr(d, "encoding", "utf-8") or "utf-8",
                "group_name": group_name,
                "credential_name": cred_name,
                "default_template_name": default_template_name,
            }
        )
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="devices.csv"'},
    )


@router.post("/devices/import.csv", summary="批量导入设备", description="通过CSV文件批量导入设备")
async def import_devices_csv(
    request: Request,
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
    mode: str = Form("insert"),
    match_by: str = Form("host_port"),
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
    try:
        result = device_service.import_devices_from_csv(
            session,
            csv_text=text,
            mode=mode,
            match_by=match_by,
        )
    except device_service.ServiceError as exc:
        if exc.code == "DEVICE_IMPORT_INVALID_COLUMNS":
            return RedirectResponse(url="/devices?err=CSV缺少必要列", status_code=303)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    for entry in result.log_entries:
        _log_action(
            request,
            session,
            entry["action"],
            entry["resource_type"],
            entry["resource_id"],
            entry["detail"],
        )

    if result.affected_device_ids:
        bulk_reachability_task.delay(device_ids=result.affected_device_ids, offset_minutes=0)

    return templates.TemplateResponse(
        request=request,
        name="import_result.html",
        context={
            **_layout_context(request=request, active="devices"),
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "mode": result.mode,
            "match_by": result.match_by,
            "report": result.report,
        },
    )


@router.post("/devices", summary="创建或更新设备", description="新增或修改设备的基本信息、连接凭据及分组")
def create_device(
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(22),
    login_method: str = Form("ssh"),
    encoding: str = Form("utf-8"),
    platform: str = Form(...),
    group_id: int = Form(0),
    credential_id: int = Form(...),
    default_template_id: int = Form(0),
):
    _require_permission(request, "devices.create")
    try:
        device = device_service.create_device(
            session,
            device_service.DeviceCreateInput(
                name=name,
                host=host,
                port=port,
                login_method=login_method,
                encoding=encoding,
                platform=platform,
                group_id=group_id,
                credential_id=credential_id,
                default_template_id=default_template_id,
            ),
        )
    except device_service.ServiceError as exc:
        if exc.code == "DEVICE_NAME_EXISTS":
            return RedirectResponse(
                url=_get_redirect_url(request, "/devices", err=f"设备名称已存在：{(name or '').strip()}"),
                status_code=303,
            )
        if exc.code == "DEVICE_HOST_EXISTS":
            return RedirectResponse(
                url=_get_redirect_url(request, "/devices", err=f"管理地址(IP+端口)已存在：{(host or '').strip()}:{port}"),
                status_code=303,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    new_device_id = device.id
    _log_action(request, session, "CREATE_DEVICE", "device", device.id, f"Name: {name}, Host: {host}")

    bulk_reachability_task.delay(device_ids=[new_device_id], offset_minutes=0)
    return RedirectResponse(url="/devices?msg=设备已创建", status_code=303)


@router.post("/devices/{device_id}/delete", summary="删除设备", description="删除指定的网络设备")
def delete_device(request: Request, device_id: int, session: Session = Depends(get_session)):
    user = _require_permission(request, "devices.delete")
    allowed_ids = get_user_allowed_group_ids(user, session=session)

    try:
        name = device_service.delete_device(session, device_id=device_id, allowed_group_ids=allowed_ids)
    except device_service.ServiceError as exc:
        if exc.code == "DEVICE_NOT_FOUND":
            return RedirectResponse(url="/devices?err=设备不存在", status_code=303)
        if exc.code == "DEVICE_DELETE_ACTIVE_BACKUPS":
            return RedirectResponse(url=_get_redirect_url(request, "/devices", err="设备存在执行中的备份任务，无法删除"), status_code=303)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _log_action(request, session, "DELETE_DEVICE", "device", device_id, f"Name: {name}")
    return RedirectResponse(url=_get_redirect_url(request, "/devices", msg="设备已删除"), status_code=303)


@router.get("/devices/{device_id}", summary="设备详情页面", description="查看指定设备的详细信息及历史备份记录")
def device_detail(request: Request, device_id: int, session: Session = Depends(get_session)):
    _require_any_permission(
        request,
        ["devices.view", "devices.create", "devices.update", "devices.delete"],
    )
    user = _current_user(request)
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    can_backup_history_view = has_permission(user, "backups.view")
    list_query = BaseListQueryInput.from_query_params(request.query_params)

    try:
        payload = device_service.get_device_detail_page_payload(
            session,
            device_id=device_id,
            page=list_query.page,
            page_size=list_query.limit,
            include_limit_param=list_query.include_limit_param,
            include_backups=can_backup_history_view,
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return templates.TemplateResponse(
        request=request,
        name="device_detail.html",
        context={
            **_layout_context(request=request, active="devices"),
            **payload,
        },
    )


@router.post("/devices/{device_id}/update", summary="更新设备", description="修改指定设备的基本信息")
def update_device(
    request: Request,
    device_id: int,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(22),
    login_method: str = Form("ssh"),
    encoding: str = Form("utf-8"),
    platform: str = Form(...),
    group_id: int = Form(0),
    credential_id: int = Form(...),
    default_template_id: int = Form(0),
):
    user = _require_permission(request, "devices.update")
    allowed_ids = get_user_allowed_group_ids(user, session=session)

    try:
        updated = device_service.update_device(
            session,
            device_id=device_id,
            data=device_service.DeviceUpdateInput(
                name=name,
                host=host,
                port=port,
                login_method=login_method,
                encoding=encoding,
                platform=platform,
                group_id=group_id,
                credential_id=credential_id,
                default_template_id=default_template_id,
            ),
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        if exc.code == "DEVICE_NAME_EXISTS":
            return RedirectResponse(
                url=_get_redirect_url(request, f"/devices/{device_id}", err=f"设备名称已存在：{(name or '').strip()}"),
                status_code=303,
            )
        if exc.code == "DEVICE_HOST_EXISTS":
            return RedirectResponse(
                url=_get_redirect_url(request, f"/devices/{device_id}", err=f"管理地址(IP+端口)已存在：{(host or '').strip()}:{port}"),
                status_code=303,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _log_action(request, session, "UPDATE_DEVICE", "device", device_id, f"Name: {name}, Host: {host}")
    
    bulk_reachability_task.delay(device_ids=[device_id], offset_minutes=0)
    return RedirectResponse(url=f"/devices/{device_id}?msg=修改已保存", status_code=303)


@router.post("/devices/{device_id}/backup", summary="手动触发备份", description="立即触发指定设备的配置备份任务")
def trigger_backup(request: Request, device_id: int, session: Session = Depends(get_session), template_id: int = Form(0)):
    _require_permission(request, "devices.view")
    _require_permission(request, "devices.backup")
    template_id = int(template_id) if template_id else 0
    try:
        result = backup_service.trigger_backup(
            session,
            device_id=device_id,
            template_id=template_id,
            skip_email=False,
            allowed_group_ids=get_user_allowed_group_ids(_current_user(request), session=session),
        )
    except backup_service.ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    _log_action(request, session, "TRIGGER_BACKUP", "device", device_id, f"Backup Record ID: {result.record_id}")
    if not result.enqueued:
        return RedirectResponse(url=f"/devices/{device_id}?err=Celery 未启用或不可用", status_code=303)
    return RedirectResponse(url=f"/devices/{device_id}?msg=备份任务已启动", status_code=303)


