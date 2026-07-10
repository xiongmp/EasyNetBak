from __future__ import annotations

import re
import logging
import os
import time
from enum import Enum, unique
from typing import Any, Callable

from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

logger = logging.getLogger(__name__)
_ENABLE_LEGACY_SSH_FALLBACK = os.getenv(
    "NETMIKO_ENABLE_LEGACY_SSH_FALLBACK",
    os.getenv("NETMIKO_ENABLE_LEGACY_HOSTKEY_FALLBACK", "1"),
).lower() not in {
    "0",
    "false",
    "no",
}

# Disable modern SSH algorithms so older devices can fall back to
# ssh-rsa/group1-sha1/cbc/hmac-sha1 when Paramiko still supports them.
_LEGACY_SSH_DISABLED_ALGORITHMS = {
    "keys": [
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "rsa-sha2-256",
        "rsa-sha2-512",
    ],
    "kex": [
        "curve25519-sha256",
        "curve25519-sha256@libssh.org",
        "ecdh-sha2-nistp256",
        "ecdh-sha2-nistp384",
        "ecdh-sha2-nistp521",
        "diffie-hellman-group-exchange-sha256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group16-sha512",
        "diffie-hellman-group18-sha512",
        "sntrup761x25519-sha512",
        "sntrup761x25519-sha512@openssh.com",
        "kex-strict-s-v00@openssh.com",
        "kex-strict-c-v00@openssh.com",
    ],
    "ciphers": [
        "aes128-ctr",
        "aes192-ctr",
        "aes256-ctr",
        "aes128-gcm@openssh.com",
        "aes256-gcm@openssh.com",
        "chacha20-poly1305@openssh.com",
    ],
    "macs": [
        "hmac-sha2-256",
        "hmac-sha2-512",
        "hmac-sha2-256-etm@openssh.com",
        "hmac-sha2-512-etm@openssh.com",
        "hmac-sha1-etm@openssh.com",
        "hmac-md5-etm@openssh.com",
        "umac-64@openssh.com",
        "umac-128@openssh.com",
        "umac-64-etm@openssh.com",
        "umac-128-etm@openssh.com",
    ],
}

_LEGACY_SSH_COMPATIBILITY_OPTIONS = {
    "disabled_algorithms": _LEGACY_SSH_DISABLED_ALGORITHMS,
    "disable_sha2_fix": True,
}

_SSH_ALGO_MISMATCH_KEYWORDS = (
    "incompatible ssh peer",
    "incompatible ssh server",
    "no acceptable host key",
    "no acceptable key exchange",
    "no acceptable kex algorithm",
    "no acceptable ciphers",
    "no acceptable macs",
    "no matching host key",
    "can't match requested host key type",
    "host key type",
    "no matching key exchange",
    "no matching kex",
    "no matching cipher",
    "no matching encryption algorithm",
    "no matching mac",
    "algorithm negotiation failed",
    "negotiation failed",
    "no common algorithms",
)


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


def _is_ssh_algo_mismatch_error(exc: Exception) -> bool:
    err_msg = str(exc).lower()
    return any(keyword in err_msg for keyword in _SSH_ALGO_MISMATCH_KEYWORDS)


def _merge_disabled_algorithms(
    existing: dict[str, Any] | None,
    extra: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}

    if isinstance(existing, dict):
        for key, value in existing.items():
            if isinstance(value, list):
                merged[key] = value.copy()

    for key, values in extra.items():
        merged_values = merged.setdefault(key, [])
        for algo in values:
            if algo not in merged_values:
                merged_values.append(algo)

    return merged


def _legacy_ssh_compatible_device(device: dict[str, Any]) -> dict[str, Any]:
    legacy_device = dict(device)
    legacy_device["disabled_algorithms"] = _merge_disabled_algorithms(
        legacy_device.get("disabled_algorithms"),
        _LEGACY_SSH_COMPATIBILITY_OPTIONS["disabled_algorithms"],
    )
    legacy_device["disable_sha2_fix"] = bool(
        _LEGACY_SSH_COMPATIBILITY_OPTIONS["disable_sha2_fix"]
    )
    return legacy_device


def _requires_enable_mode(device_type: str) -> bool:
    normalized = (device_type or "").lower()
    return normalized.startswith(("cisco", "ruijie", "zte", "maipu", "huawei_olt"))


def _clean_backspaces(text: str) -> str:
    """处理退格符 \x08 和 \x7f 以模拟终端输出，保证输出无多余空格或乱码"""
    chars = []
    for char in text:
        if char in ('\x08', '\x7f'):
            if chars:
                chars.pop()
        else:
            chars.append(char)
    return "".join(chars)


def _tail_has_prompt(buffer_tail: str, prompt: str) -> bool:
    if not prompt:
        return False
    return bool(re.search(rf"{re.escape(prompt)}\s*$", buffer_tail))


def _tail_has_pagination_marker(buffer_tail: str, pagination_pattern: str) -> bool:
    if not buffer_tail:
        return False
    lines = buffer_tail.splitlines()
    tail_line = lines[-1] if lines else buffer_tail
    return bool(re.search(rf"{pagination_pattern}\s*$", tail_line))


def _is_meaningful_pagination_chunk(chunk: str, pagination_pattern: str) -> bool:
    if not chunk:
        return False

    normalized = _clean_backspaces(chunk)
    normalized = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", normalized)
    normalized = re.sub(pagination_pattern, "", normalized)
    normalized = normalized.strip()
    return bool(normalized)


def _raise_netmiko_error(exc: Exception, default_message: str) -> None:
    if isinstance(exc, NetmikoClientError):
        raise exc

    err_msg = str(exc).lower()
    
    # Mapping of partial string matches to ErrorCode
    mapping = [
        ("incompatible ssh peer", NetmikoErrorCode.ALGO_MISMATCH),
        ("incompatible ssh server", NetmikoErrorCode.ALGO_MISMATCH),
        ("negotiation failed", NetmikoErrorCode.ALGO_MISMATCH),
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
        ("no acceptable key exchange", NetmikoErrorCode.ALGO_MISMATCH),
        ("no acceptable kex algorithm", NetmikoErrorCode.ALGO_MISMATCH),
        ("no acceptable ciphers", NetmikoErrorCode.ALGO_MISMATCH),
        ("no acceptable macs", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching key exchange", NetmikoErrorCode.ALGO_MISMATCH),
        ("no common algorithms", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching cipher", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching encryption algorithm", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching mac", NetmikoErrorCode.ALGO_MISMATCH),
        ("no acceptable host key", NetmikoErrorCode.ALGO_MISMATCH),
        ("no matching host key", NetmikoErrorCode.ALGO_MISMATCH),
        ("can't match requested host key type", NetmikoErrorCode.ALGO_MISMATCH),
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
    read_timeout_override: int = 120,
    command_read_timeout: int = 240,
    command_max_loops: int = 120,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> str:
    from app.platforms import to_netmiko_device_type

    start_time = time.time()
    logger.info(f"Starting backup for {host}:{port} ({platform})")

    def emit_progress(event: str, **details: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(event, details)
        except Exception:
            logger.debug("Backup progress callback failed", exc_info=True)

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
        emit_progress(
            "backup_record_netmiko_connecting",
            host=host,
            port=port,
            platform=platform,
            login_method=login_method,
            device_type=conn_device.get("device_type"),
            encoding=conn_device.get("encoding"),
            conn_timeout=conn_timeout,
            auth_timeout=auth_timeout,
            banner_timeout=banner_timeout,
        )
        with ConnectHandler(**conn_device) as conn:
            emit_progress("backup_record_netmiko_connected", host=host, port=port)
            conn.ansi_escape_codes = False
            if _requires_enable_mode(device_type):
                emit_progress("backup_record_enable_started")
                conn.enable()
                emit_progress("backup_record_enable_completed")
            prompt = conn.find_prompt().rstrip()
            emit_progress("backup_record_prompt_detected", prompt=prompt)
            
            # 常见的分页提示符正则，如 "---- More ----", "--More--", "More:"
            pagination_pattern = r"(?:--\s*[Mm][Oo][Rr][Ee]\s*--|----\s*[Mm][Oo][Rr][Ee]\s*----|[Mm][Oo][Rr][Ee]:)"
            # 联合匹配设备提示符或分页提示符，用于及时发现分页异常
            expect_pattern = rf"({re.escape(prompt)}|{pagination_pattern})"

            total_commands = len(commands)
            for index, cmd in enumerate(commands, start=1):
                command_start = time.time()
                emit_progress(
                    "backup_record_command_started",
                    command=cmd,
                    command_index=index,
                    command_count=total_commands,
                    read_timeout=command_read_timeout,
                )
                # 默认 send_command 获取输出，利用 expect_pattern 发现分页异常时不等待超时立即返回
                out = conn.send_command(
                    cmd,
                    expect_string=expect_pattern,
                    strip_prompt=False,
                    strip_command=False,
                    read_timeout=command_read_timeout,
                    delay_factor=global_delay_factor,
                    max_loops=command_max_loops,
                )

                # 发现分页异常时，自动切到 send_command_timing() 循环处理
                if re.search(pagination_pattern, out):
                    emit_progress(
                        "backup_record_command_pagination_detected",
                        command=cmd,
                        command_index=index,
                    )
                    logger.info(f"发现分页异常 (设备 {host}:{port})，命令 '{cmd}' 自动切到 send_command_timing() 并手动按空格处理...")
                    full_output = [out]
                    buffer_tail = out
                    output_size = len(out.encode(device["encoding"], errors="ignore"))
                    page_reads = 0

                    # 将超时语义改为空闲超时，避免超大配置在持续输出时被总耗时误杀。
                    last_data_time = time.monotonic()
                    pagination_start = last_data_time
                    absolute_timeout = max(command_read_timeout * 3, command_read_timeout + 120)
                    max_output_bytes = 10 * 1024 * 1024

                    while not _tail_has_prompt(buffer_tail, prompt):
                        now = time.monotonic()
                        if now - last_data_time > command_read_timeout:
                            logger.error(f"处理分页时空闲超时 ({command_read_timeout}s) - 设备: {host}:{port}")
                            raise NetmikoTimeoutException(f"处理分页空闲超时，未能找到提示符 '{prompt}'")
                        if now - pagination_start > absolute_timeout:
                            logger.error(f"处理分页时绝对超时 ({absolute_timeout}s) - 设备: {host}:{port}")
                            raise NetmikoTimeoutException(f"处理分页总耗时过长，未能找到提示符 '{prompt}'")

                        if _tail_has_pagination_marker(buffer_tail, pagination_pattern):
                            page_reads += 1
                            # 当前处于分页符，发送空格 (注意 normalize=False 防止发送回车)
                            page_out = conn.send_command_timing(
                                " ",
                                strip_prompt=False,
                                strip_command=False,
                                normalize=False,
                                delay_factor=global_delay_factor,
                            )
                        else:
                            # 没到分页符，也没到 prompt，可能是输出较慢，继续提取缓冲数据
                            page_out = conn.send_command_timing(
                                "",
                                strip_prompt=False,
                                strip_command=False,
                                normalize=False,
                                delay_factor=global_delay_factor,
                            )

                        if page_out:
                            full_output.append(page_out)
                            output_size += len(page_out.encode(device["encoding"], errors="ignore"))
                            if output_size > max_output_bytes:
                                logger.error(f"处理分页时输出超限 ({max_output_bytes} bytes) - 设备: {host}:{port}")
                                raise NetmikoTimeoutException("处理分页时输出过大，已超过安全限制")

                            combined_output = "".join(full_output)
                            # 保留尾部用于正则检测，避免历史分页标记干扰当前判断。
                            buffer_tail = combined_output[-1000:]

                            if _is_meaningful_pagination_chunk(page_out, pagination_pattern) or _tail_has_prompt(buffer_tail, prompt):
                                last_data_time = time.monotonic()

                    out = "".join(full_output)
                    emit_progress(
                        "backup_record_command_pagination_completed",
                        command=cmd,
                        command_index=index,
                        page_reads=page_reads,
                        output_bytes=output_size,
                    )

                # 处理退格符 \x08 和 \x7f 
                out = _clean_backspaces(out)
                
                # 处理 ANSI 序列形式的分页擦除 (如 \x1b[16D 等) 或 H3C 等设备的回车覆盖模式 (\n\n   \n)
                clear_seq_ansi = r"(?:\x1b\[[0-9;]*[a-zA-Z][ \t]*\x1b\[[0-9;]*[a-zA-Z])"
                clear_seq_h3c = r"(?:\n+[ \t]+\n+)"
                clear_seq = rf"(?:{clear_seq_ansi}|{clear_seq_h3c})?"
                out = re.sub(r"[ \t]*" + pagination_pattern + r"[ \t]*" + clear_seq, "", out)

                # 然后再剥离剩余的普通 ANSI 序列
                out = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", out)

                # 如果末尾残留了 prompt (因为 strip_prompt=False)，则将其剥离
                if out.endswith(prompt):
                    out = out[:-len(prompt)]
                elif prompt in out:
                    out = out.rsplit(prompt, 1)[0]

                # Normalize command echo line to "<prompt><command>" format when device only echoes command.
                lines = out.splitlines()
                if lines and lines[0].strip() == cmd.strip():
                    lines[0] = f"{prompt}{cmd}"
                    out = "\n".join(lines)
                output_parts.append(out.rstrip())
                emit_progress(
                    "backup_record_command_completed",
                    command=cmd,
                    command_index=index,
                    command_count=total_commands,
                    line_count=len((out or "").splitlines()),
                    output_bytes=len((out or "").encode(device["encoding"], errors="ignore")),
                    duration_seconds=round(time.time() - command_start, 3),
                )
            emit_progress("backup_record_netmiko_disconnected", host=host, port=port)
        return output_parts

    try:
        output_parts = _execute(device)
    except Exception as exc:
        if _ENABLE_LEGACY_SSH_FALLBACK and _is_ssh_algo_mismatch_error(exc):
            emit_progress("backup_record_legacy_ssh_fallback_started", error=str(exc))
            logger.warning(
                "Detected SSH algorithm mismatch for %s:%s, retrying with legacy SSH compatibility mode: %s",
                host,
                port,
                exc,
            )
            try:
                output_parts = _execute(_legacy_ssh_compatible_device(device))
                emit_progress("backup_record_legacy_ssh_fallback_completed")
            except Exception as retry_exc:
                duration = time.time() - start_time
                logger.error(f"Backup failed for {host}:{port} after {duration:.2f}s: {retry_exc}")
                emit_progress(
                    "backup_record_netmiko_failed",
                    error=str(retry_exc),
                    duration_seconds=round(duration, 3),
                )
                _raise_netmiko_error(retry_exc, "备份失败")
        else:
            duration = time.time() - start_time
            logger.error(f"Backup failed for {host}:{port} after {duration:.2f}s: {exc}")
            emit_progress(
                "backup_record_netmiko_failed",
                error=str(exc),
                duration_seconds=round(duration, 3),
            )
            _raise_netmiko_error(exc, "备份失败")

    duration = time.time() - start_time
    logger.info(f"Backup completed for {host}:{port} in {duration:.2f}s")
    emit_progress("backup_record_netmiko_completed", duration_seconds=round(duration, 3))

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
            if _requires_enable_mode(device_type):
                conn.enable()
            return conn.find_prompt()

    try:
        return _probe(device)
    except Exception as exc:
        if _ENABLE_LEGACY_SSH_FALLBACK and _is_ssh_algo_mismatch_error(exc):
            logger.warning(
                "Detected SSH algorithm mismatch for %s:%s, retrying test connection with legacy SSH compatibility mode: %s",
                host,
                port,
                exc,
            )
            try:
                return _probe(_legacy_ssh_compatible_device(device))
            except Exception as retry_exc:
                _raise_netmiko_error(retry_exc, "连接失败")
        _raise_netmiko_error(exc, "连接失败")
