from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

netmiko_stub = types.ModuleType("netmiko")
netmiko_stub.ConnectHandler = None
netmiko_stub.NetmikoTimeoutException = type("NetmikoTimeoutException", (Exception,), {})
netmiko_stub.NetmikoAuthenticationException = type("NetmikoAuthenticationException", (Exception,), {})
sys.modules.setdefault("netmiko", netmiko_stub)

from app.services import netmiko_client


class _FakeConnection:
    def __init__(self) -> None:
        self.enable_calls = 0
        self.ansi_escape_codes = True

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def enable(self) -> None:
        self.enable_calls += 1

    def find_prompt(self) -> str:
        return "RG#"

    def send_command(self, command: str, **kwargs) -> str:
        return f"{command}\ncurrent configuration\nRG#"


@pytest.mark.parametrize(
    ("runner", "kwargs"),
    [
        (
            netmiko_client.run_netmiko_commands,
            {"commands": ["show running-config"]},
        ),
        (
            netmiko_client.test_netmiko_connection,
            {},
        ),
    ],
)
def test_ruijie_enable_password_enters_enable_mode(monkeypatch, runner, kwargs):
    fake_conn = _FakeConnection()

    def fake_connect_handler(**device):
        assert device["device_type"] == "ruijie_os"
        assert device["secret"] == "enable"
        return fake_conn

    monkeypatch.setattr(netmiko_client, "ConnectHandler", fake_connect_handler)

    runner(
        host="10.0.0.10",
        port=22,
        platform="ruijie_os",
        login_method="ssh",
        username="admin",
        password="password",
        enable_password="enable",
        **kwargs,
    )

    assert fake_conn.enable_calls == 1


@pytest.mark.parametrize(
    ("runner", "kwargs"),
    [
        (
            netmiko_client.run_netmiko_commands,
            {"commands": ["show running-config"]},
        ),
        (
            netmiko_client.test_netmiko_connection,
            {},
        ),
    ],
)
def test_legacy_ssh_fallback_retries_on_no_acceptable_kex(monkeypatch, runner, kwargs):
    fake_conn = _FakeConnection()
    connect_calls: list[dict[str, object]] = []

    def fake_connect_handler(**device):
        connect_calls.append(device)
        if len(connect_calls) == 1:
            raise Exception(
                "A paramiko SSHException occurred during connection creation: "
                "Incompatible ssh peer (no acceptable kex algorithm)"
            )
        assert device["disabled_algorithms"] == netmiko_client._LEGACY_SSH_DISABLED_ALGORITHMS
        return fake_conn

    monkeypatch.setattr(netmiko_client, "ConnectHandler", fake_connect_handler)

    result = runner(
        host="10.0.0.10",
        port=22,
        platform="ruijie_os",
        login_method="ssh",
        username="admin",
        password="password",
        enable_password="enable",
        **kwargs,
    )

    assert len(connect_calls) == 2
    assert "disabled_algorithms" not in connect_calls[0]
    if runner is netmiko_client.run_netmiko_commands:
        assert result == "RG#show running-config\ncurrent configuration\n"
    else:
        assert result == "RG#"
