from __future__ import annotations

from app.platforms import DEFAULT_COMMANDS, normalize_platform_id
from app.services.netmiko_client import run_netmiko_commands


def normalize_commands(commands_text: str) -> list[str]:
    commands: list[str] = []
    for line in (commands_text or "").splitlines():
        cmd = line.strip()
        if not cmd:
            continue
        commands.append(cmd)
    return commands


def backup_device(
    *,
    host: str,
    port: int,
    login_method: str,
    encoding: str,
    platform: str,
    username: str,
    password: str | None,
    enable_password: str | None,
    template_commands: str | None,
) -> str:
    commands_text = (template_commands or "").strip() or DEFAULT_COMMANDS.get(normalize_platform_id(platform), "")
    commands = normalize_commands(commands_text)
    if not commands:
        raise RuntimeError("No commands configured for this platform")
    return run_netmiko_commands(
        host=host,
        port=port,
        login_method=login_method,
        encoding=encoding,
        platform=platform,
        username=username,
        password=password,
        enable_password=enable_password,
        commands=commands,
    )
