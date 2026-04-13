from __future__ import annotations

import logging
import os
import time
from enum import Enum, unique
from typing import Any

from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

logger = logging.getLogger(__name__)
_ENABLE_LEGACY_HOSTKEY_FALLBACK = os.getenv("NETMIKO_ENABLE_LEGACY_HOSTKEY_FALLBACK", "1").lower() not in {
    "0",
    "false",
    "no",
}

# Disable modern host-key algorithms so legacy devices can negotiate ssh-rsa/ssh-dss.
_LEGACY_HOSTKEY_DISABLED_ALGORITHMS = {
    "keys": [
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "rsa-sha2-256",
        "rsa-sha2-512",
    ]
}


@unique
class NetmikoErrorCode(Enum):
    UNKNOWN = (1000, "未知错误")
    TIMEOUT = (1001, "连接超时")
    AUTH_FAILED = (1002, "认证失败")
    REFUSED = (1003, "连接被拒绝")
    PRIVILEGE_FAILED = (1004, "特权模式失败")
    READ_TIMEOUT = (1005, "读取超时")
    DISCONNECTED = (1006, "连接断开")
    KEY_ERROR = (1007, "密钥错误")
    PERMISSION_DENIED = (1008, "权限不足")
    PROTOCOL_ERROR = (1009, "协议/命令错误")
    PROMPT_ERROR = (1010, "提示符错误")
    DNS_ERROR = (1011, "域名解析失败")
    NETWORK_UNREACHABLE = (1012, "网络不可达")
    ALGO_MISMATCH = (1013, "算法不匹配")
    SESSION_LIMIT = (1014, "会话超限")
    
    def __init__(self, code: int, description: str):
        self.code = code
        self.description = description


class NetmikoClientError(RuntimeError):
    def __init__(
        self, 
        message: str, 
        failure_type: str | None = None, 
        error_code: NetmikoErrorCode = NetmikoErrorCode.UNKNOWN
    ):
        super().__init__(message)
        self.error_code = error_code
        # If failure_type is explicitly provided, use it (for backward compatibility)
        # Otherwise, derive from error_code
        if failure_type:
            self.failure_type = failure_type
        else:
            self.failure_type = error_code.name

    @property
    def code(self) -> int:
        return self.error_code.code


def _is_hostkey_algo_mismatch_error(exc: Exception) -> bool:
    err_msg = str(exc).lower()
    keywords = (
        "incompatible ssh peer",
        "no acceptable host key",
        "no matching host key",
        "host key type",
    )
    return any(keyword in err_msg for keyword in keywords)


def _legacy_hostkey_compatible_device(device: dict[str, Any]) -> dict[str, Any]:
    legacy_device = dict(device)
    existing = legacy_device.get("disabled_algorithms")
    merged: dict[str, list[str]] = {}

    if isinstance(existing, dict):
        for key, value in existing.items():
            if isinstance(value, list):
                merged[key] = value.copy()

    legacy_keys = merged.setdefault("keys", [])
    for algo in _LEGACY_HOSTKEY_DISABLED_ALGORITHMS["keys"]:
        if algo not in legacy_keys:
            legacy_keys.append(algo)

    legacy_device["disabled_algorithms"] = merged
    return legacy_device


def _raise_netmiko_error(exc: Exception, default_message: str) -> None:
    if isinstance(exc, NetmikoClientError):
        raise exc

    err_msg = str(exc).lower()
    
    # Mapping of partial string matches to ErrorCode
    mapping = [
        ("connection refused", NetmikoErrorCode.REFUSED),
        ("authentication failed", NetmikoErrorCode.AUTH_FAILED),
        ("timed out", NetmikoErrorCode.TIMEOUT),
        ("read timeout", NetmikoErrorCode.READ_TIMEOUT),
        ("eof", NetmikoErrorCode.DISCONNECTED),
        ("reset by peer", NetmikoErrorCode.DISCONNECTED),
        ("no existing session", NetmikoErrorCode.DISCONNECTED),
        ("ssh key", NetmikoErrorCode.KEY_ERROR),
        ("private key", NetmikoErrorCode.KEY_ERROR),
        ("authorization failed", NetmikoErrorCode.PERMISSION_DENIED),
        ("permission denied", NetmikoErrorCode.PERMISSION_DENIED),
        ("unsupported", NetmikoErrorCode.PROTOCOL_ERROR),
        ("not found", NetmikoErrorCode.PROTOCOL_ERROR),
        ("search pattern", NetmikoErrorCode.PROMPT_ERROR),
        ("prompt", NetmikoErrorCode.PROMPT_ERROR),
        ("getaddrinfo failed", NetmikoErrorCode.DNS_ERROR),
        ("name or service not known", NetmikoErrorCode.DNS_ERROR),
        ("no route to host", NetmikoErrorCode.NETWORK_UNREACHABLE),
        ("network is unreachable", NetmikoErrorCode.NETWORK_UNREACHABLE),
        ("kex error", NetmikoErrorCode.ALGO_MISMATCH),
        ("kex_exchange_identification", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching key exchange", NetmikoErrorCode.ALGO_MISMATCH),
        ("no common algorithms", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching cipher", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching mac", NetmikoErrorCode.ALGO_MISMATCH),
        ("no acceptable host key", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching host key", NetmikoErrorCode.ALGO_MISMATCH),
        ("host key type", NetmikoErrorCode.ALGO_MISMATCH),
        ("incompatible version", NetmikoErrorCode.ALGO_MISMATCH),
        ("max sessions", NetmikoErrorCode.SESSION_LIMIT),
        ("too many connections", NetmikoErrorCode.SESSION_LIMIT),
    ]

    # Special check for enable password failure which has complex logic
    if "enable" in err_msg and ("password" in err_msg or "secret" in err_msg):
        raise NetmikoClientError(
            f"特权模式失败: Enable 密码错误 ({str(exc)})", 
            error_code=NetmikoErrorCode.PRIVILEGE_FAILED
        )

    msg_map = {
        NetmikoErrorCode.REFUSED: "连接被拒绝: 目标端口未开放或防火墙拦截",
        NetmikoErrorCode.AUTH_FAILED: "认证失败: 账号或密码错误",
        NetmikoErrorCode.TIMEOUT: "连接超时: 网络不通或响应缓慢",
        NetmikoErrorCode.PRIVILEGE_FAILED: "特权模式失败: Enable 密码错误",
        NetmikoErrorCode.READ_TIMEOUT: "读取超时: 设备未在规定时间内返回数据",
        NetmikoErrorCode.DISCONNECTED: "连接断开: 远程主机强制关闭了连接",
        NetmikoErrorCode.KEY_ERROR: "密钥错误: SSH 私钥文件无效或格式错误",
        NetmikoErrorCode.PERMISSION_DENIED: "权限不足: 账号权限不足以执行备份命令",
        NetmikoErrorCode.PROTOCOL_ERROR: "协议/命令错误: 设备不支持该平台协议或命令",
        NetmikoErrorCode.PROMPT_ERROR: "提示符错误: 无法识别设备提示符或登录未完成",
        NetmikoErrorCode.DNS_ERROR: "域名解析失败: 无法解析设备主机名",
        NetmikoErrorCode.NETWORK_UNREACHABLE: "网络不可达: 路由不可达或网络中断",
        NetmikoErrorCode.ALGO_MISMATCH: "算法不匹配: 设备 SSH 版本过低或加密算法不支持",
        NetmikoErrorCode.SESSION_LIMIT: "会话超限: 设备 SSH 连接数已达上限",
    }

    for keyword, ec in mapping:
        if keyword in err_msg:
            base_msg = msg_map.get(ec, default_message)
            raise NetmikoClientError(f"{base_msg} ({str(exc)})", error_code=ec)
    
    # If no string match, fall back to exception type checks
    if isinstance(exc, NetmikoTimeoutException):
        raise NetmikoClientError(
            f"连接超时: 设备不可达或端口不通 ({str(exc)})", 
            error_code=NetmikoErrorCode.TIMEOUT
        )
    if isinstance(exc, NetmikoAuthenticationException):
        raise NetmikoClientError(
            f"认证失败: 账号或密码错误 ({str(exc)})", 
            error_code=NetmikoErrorCode.AUTH_FAILED
        )

    raise NetmikoClientError(f"{default_message}: {str(exc)}", error_code=NetmikoErrorCode.UNKNOWN)


def run_netmiko_commands(
    *,
    host: str,
    port: int,
    platform: str,
    login_method: str,
    encoding: str = "utf-8",
    username: str,
    password: str | None,
    enable_password: str | None,
    commands: list[str],
    conn_timeout: int = 30,
    banner_timeout: int = 60,
    global_delay_factor: float = 2.0,
    auth_timeout: int = 45,
    read_timeout_override: int = 60,
    command_read_timeout: int = 180,
    command_max_loops: int = 120,
) -> str:
    from app.platforms import to_netmiko_device_type

    start_time = time.time()
    logger.info(f"Starting backup for {host}:{port} ({platform})")

    device_type = to_netmiko_device_type(platform, login_method)
    device: dict[str, Any] = {
        "device_type": device_type,
        "host": host,
        "port": port,
        "username": username,
        "encoding": (encoding or "utf-8"),
        "timeout": conn_timeout,
        "banner_timeout": banner_timeout,
        "global_delay_factor": global_delay_factor,
        "read_timeout_override": read_timeout_override,
    }
    device["auth_timeout"] = auth_timeout
    device["conn_timeout"] = conn_timeout
    device["allow_agent"] = False
    device["use_keys"] = False
    if "huawei" in device_type:
        device["fast_cli"] = False

    if password:
        device["password"] = password
    if enable_password:
        device["secret"] = enable_password

    def _execute(conn_device: dict[str, Any]) -> list[str]:
        output_parts: list[str] = []
        with ConnectHandler(**conn_device) as conn:
            if device_type.startswith("cisco") and enable_password:
                conn.enable()
            for cmd in commands:
                out = conn.send_command(
                    cmd,
                    strip_prompt=True,
                    strip_command=True,
                    read_timeout=command_read_timeout,
                    delay_factor=global_delay_factor,
                    max_loops=command_max_loops,
                )
                output_parts.append(out.rstrip())
        return output_parts

    try:
        output_parts = _execute(device)
    except Exception as exc:
        if _ENABLE_LEGACY_HOSTKEY_FALLBACK and _is_hostkey_algo_mismatch_error(exc):
            logger.warning("Detected host key algorithm mismatch for %s:%s, retrying with legacy hostkey compatibility mode", host, port)
            try:
                output_parts = _execute(_legacy_hostkey_compatible_device(device))
            except Exception as retry_exc:
                duration = time.time() - start_time
                logger.error(f"Backup failed for {host}:{port} after {duration:.2f}s: {retry_exc}")
                _raise_netmiko_error(retry_exc, "备份失败")
        else:
            duration = time.time() - start_time
            logger.error(f"Backup failed for {host}:{port} after {duration:.2f}s: {exc}")
            _raise_netmiko_error(exc, "备份失败")

    duration = time.time() - start_time
    logger.info(f"Backup completed for {host}:{port} in {duration:.2f}s")

    return "\n\n".join(output_parts).strip() + "\n"


def test_netmiko_connection(
    *,
    host: str,
    port: int,
    platform: str,
    login_method: str,
    encoding: str = "utf-8",
    username: str,
    password: str | None,
    enable_password: str | None,
    conn_timeout: int = 30,
    banner_timeout: int = 60,
    global_delay_factor: float = 2.0,
    auth_timeout: int = 45,
    read_timeout_override: int = 60,
) -> str:
    from app.platforms import to_netmiko_device_type

    device_type = to_netmiko_device_type(platform, login_method)
    device: dict[str, Any] = {
        "device_type": device_type,
        "host": host,
        "port": port,
        "username": username,
        "encoding": (encoding or "utf-8"),
        "timeout": conn_timeout,
        "banner_timeout": banner_timeout,
        "global_delay_factor": global_delay_factor,
        "read_timeout_override": read_timeout_override,
    }
    device["auth_timeout"] = auth_timeout
    device["conn_timeout"] = conn_timeout
    device["allow_agent"] = False
    device["use_keys"] = False
    if "huawei" in device_type:
        device["fast_cli"] = False

    if password:
        device["password"] = password
    if enable_password:
        device["secret"] = enable_password

    def _probe(conn_device: dict[str, Any]) -> str:
        with ConnectHandler(**conn_device) as conn:
            if device_type.startswith("cisco") and enable_password:
                conn.enable()
            return conn.find_prompt()

    try:
        return _probe(device)
    except Exception as exc:
        if _ENABLE_LEGACY_HOSTKEY_FALLBACK and _is_hostkey_algo_mismatch_error(exc):
            logger.warning("Detected host key algorithm mismatch for %s:%s, retrying test connection with legacy hostkey compatibility mode", host, port)
            try:
                return _probe(_legacy_hostkey_compatible_device(device))
            except Exception as retry_exc:
                _raise_netmiko_error(retry_exc, "连接失败")
        _raise_netmiko_error(exc, "连接失败")
