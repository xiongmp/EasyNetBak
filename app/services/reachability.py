from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any

from app import crud
from app.db import session_scope
from app.core.time import format_local_datetime
from app.services.netmiko_client import test_netmiko_connection


def perform_single_reachability_check(device_id: int, offset_minutes: int = 0) -> dict[str, Any] | None:
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            return None

        secrets = crud.get_device_secrets(session, device)
        if not secrets.get("username"):
            device.reachability_status = False
            device.reachability_error = "未配置凭据"
            device.last_reachability_check = datetime.utcnow()
            session.add(device)
            return {
                "id": device.id,
                "name": device.name,
                "host": device.host,
                "success": False,
                "error_message": "未配置凭据",
                "duration_ms": 0,
                "last_checked": format_local_datetime(device.last_reachability_check, offset_minutes=offset_minutes),
                "login_method": device.login_method,
            }

        started = time.monotonic()
        
        # 1. Socket 端口预检 (快速失败)
        socket_open = False
        try:
            # 使用较短的超时时间 (3秒) 进行端口探测
            with socket.create_connection((device.host, device.port), timeout=3):
                socket_open = True
        except (OSError, socket.timeout):
            pass

        if not socket_open:
            device.reachability_status = False
            device.reachability_error = f"端口不可达 ({device.host}:{device.port})"
            device.reachability_duration_ms = int((time.monotonic() - started) * 1000)
            device.last_reachability_check = datetime.utcnow()
            session.add(device)

            return {
                "id": device.id,
                "name": device.name,
                "host": device.host,
                "success": False,
                "error_message": device.reachability_error,
                "duration_ms": device.reachability_duration_ms,
                "last_checked": format_local_datetime(device.last_reachability_check, offset_minutes=offset_minutes),
                "login_method": device.login_method,
            }

        # 2. 尝试建立完整连接 (Netmiko)
        try:
            test_netmiko_connection(
                host=device.host,
                port=device.port,
                login_method=device.login_method,
                encoding=getattr(device, "encoding", "utf-8") or "utf-8",
                platform=device.platform,
                username=secrets["username"],
                password=secrets["password"],
                enable_password=secrets["enable_password"],
            )
            device.reachability_status = True
            device.reachability_error = None
        except Exception as exc:
            device.reachability_status = False
            device.reachability_error = str(exc)

        device.reachability_duration_ms = int((time.monotonic() - started) * 1000)
        device.last_reachability_check = datetime.utcnow()
        session.add(device)

        return {
            "id": device.id,
            "name": device.name,
            "host": device.host,
            "success": device.reachability_status,
            "error_message": device.reachability_error,
            "duration_ms": device.reachability_duration_ms,
            "last_checked": format_local_datetime(device.last_reachability_check, offset_minutes=offset_minutes),
            "login_method": device.login_method,
        }
