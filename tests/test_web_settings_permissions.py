from __future__ import annotations

from contextlib import contextmanager
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import crud
from app import db as app_db
from app.core.settings import settings
from app.main import app
from app.models import Credential, Device
import app.main as app_main
import app.routers.web.system as web_system
from app.services.auth import create_session_token


@pytest.fixture()
def sqlite_web_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test_web_settings_permissions.sqlite"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @contextmanager
    def test_session_scope():
        with Session(test_engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def test_init_db():
        SQLModel.metadata.create_all(test_engine)

    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "session_scope", test_session_scope)
    monkeypatch.setattr(app_db, "init_db", test_init_db)

    monkeypatch.setattr(app_main, "engine", test_engine)
    monkeypatch.setattr(app_main, "session_scope", test_session_scope)
    monkeypatch.setattr(app_main, "init_db", test_init_db)

    monkeypatch.setattr(web_system, "session_scope", test_session_scope)

    test_init_db()
    with test_session_scope() as session:
        crud.ensure_default_roles(session)

    return test_session_scope


def _create_user_with_permissions(
    sqlite_web_env,
    *,
    permission_codes: list[str],
    group_access_type: str = "all",
    allowed_group_ids: str | None = None,
) -> int:
    role_code = f"role_{uuid4().hex[:10]}"
    username = f"user_{uuid4().hex[:10]}"

    with sqlite_web_env() as session:
        role = crud.create_role(
            session,
            code=role_code,
            name=role_code,
            permissions=crud.permission_codes_to_str(permission_codes),
        )
        user = crud.create_user(
            session,
            username=username,
            password="TempPass123!",
            role=role.code,
            group_access_type=group_access_type,
            allowed_group_ids=allowed_group_ids,
        )
        return int(user.id)


def _login_client_as_user(client: TestClient, *, user_id: int) -> None:
    token = create_session_token(user_id=user_id, ttl_seconds=settings.session_ttl_seconds)
    client.cookies.set(settings.auth_cookie_name, token)


def test_settings_view_permission_shows_backup_section_in_readonly_mode(sqlite_web_env):
    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["settings.view"])

    with sqlite_web_env() as session:
        crud.set_setting(session, key="timezone_offset", value="+08:00")
        crud.set_setting(session, key="max_concurrent_tasks", value="12")
        crud.set_setting(session, key="backup_max_retries", value="4")
        crud.set_setting(session, key="backup_retry_backoff", value="30")
        crud.set_setting(session, key="task_time_limit", value="600")
        crud.set_setting(session, key="backup_retention_days", value="120")

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.get("/settings")

    assert response.status_code == 200
    assert "备份设置" in response.text
    assert "最大并发任务数" in response.text
    assert "失败重试次数" in response.text
    assert "重试间隔基数" in response.text
    assert "任务超时时间" in response.text
    assert "当前为只读权限，无法修改系统设置。" in response.text


def test_storage_view_permission_shows_readonly_storage_details(sqlite_web_env):
    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["storage_settings.view"])

    with sqlite_web_env() as session:
        crud.set_setting(session, key="s3_enabled", value="1")
        crud.set_setting(session, key="s3_endpoint", value="https://s3.example.com")
        crud.set_setting(session, key="s3_bucket", value="network-backups")
        crud.set_setting(session, key="s3_region", value="ap-guangzhou")
        crud.set_setting(session, key="s3_prefix", value="nightly")
        crud.set_setting(session, key="ftp_enabled", value="1")
        crud.set_setting(session, key="ftp_host", value="ftp.example.com")
        crud.set_setting(session, key="ftp_port", value="2121")
        crud.set_setting(session, key="ftp_username", value="backup-user")
        crud.set_setting(session, key="ftp_base_dir", value="/archives")
        crud.set_setting(session, key="ftp_passive", value="0")
        crud.set_setting(session, key="ftp_timeout", value="45")

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.get("/storage-settings")

    assert response.status_code == 200
    assert "存储配置概览" in response.text
    assert "S3 对象存储" in response.text
    assert "https://s3.example.com" in response.text
    assert "network-backups" in response.text
    assert "FTP 存储" in response.text
    assert "ftp.example.com" in response.text
    assert "当前为只读权限，可查看但不能修改存储设置。" in response.text
    assert "您没有权限修改存储设置。" not in response.text


def test_backups_page_filters_devices_by_allowed_groups(sqlite_web_env):
    with sqlite_web_env() as session:
        group_a = crud.create_group(session, name="分组A")
        group_b = crud.create_group(session, name="分组B")

        device_a = crud.create_device(
            session,
            device=Device(name="allowed-device-a", host="10.0.0.1", platform="ios", group_id=group_a.id),
        )
        device_b = crud.create_device(
            session,
            device=Device(name="forbidden-device-b", host="10.0.0.2", platform="ios", group_id=group_b.id),
        )

        backup_a = crud.create_backup_record(session, device_id=int(device_a.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=backup_a.id,
            success=True,
            config_text="hostname allowed-device-a",
            error_message=None,
        )
        backup_b = crud.create_backup_record(session, device_id=int(device_b.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=backup_b.id,
            success=True,
            config_text="hostname forbidden-device-b",
            error_message=None,
        )

    user_id = _create_user_with_permissions(
        sqlite_web_env,
        permission_codes=["backups.view"],
        group_access_type="specific",
        allowed_group_ids=str(group_a.id),
    )

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.get("/backups")

    assert response.status_code == 200
    assert "allowed-device-a" in response.text
    assert "forbidden-device-b" not in response.text


def test_device_detail_honors_update_permission_without_backup_permission(sqlite_web_env):
    with sqlite_web_env() as session:
        credential = crud.create_credential(
            session,
            credential=Credential(
                name="test-credential",
                username="tester",
                password="Secret123!",
            ),
        )
        device = crud.create_device(
            session,
            device=Device(
                name="editable-device",
                host="10.0.0.10",
                platform="ios",
                credential_id=credential.id,
            ),
        )

    user_id = _create_user_with_permissions(
        sqlite_web_env,
        permission_codes=["devices.view", "devices.update"],
    )

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.get(f"/devices/{device.id}")

    assert response.status_code == 200
    assert "保存修改" in response.text
    assert "当前账号无权限修改设备" not in response.text
    assert "当前账号无权限执行备份" in response.text
    assert "当前账号无权限查看备份历史" in response.text
    assert 'id="device-backups-tbody"' not in response.text


def test_diff_rules_page_allows_delete_only_mode(sqlite_web_env):
    with sqlite_web_env() as session:
        crud.set_setting(
            session,
            key="diff_ignore_rules",
            value=json.dumps(
                [
                    {"scope": "global", "targets": [], "patterns": ["^ntp clock-period.*"]},
                ],
                ensure_ascii=False,
            ),
        )

    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["diff_rules.delete"])

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.get("/diff-rules")

    assert response.status_code == 200
    assert "当前账号仅可删除已有规则，不能新增或修改规则内容。" in response.text
    assert "新增规则" not in response.text
    assert "重置为默认" not in response.text
    assert "保存更改" in response.text


def test_diff_rules_delete_permission_can_remove_rules(sqlite_web_env):
    original_rules = [
        {"scope": "global", "targets": [], "patterns": ["^ntp clock-period.*"]},
        {"scope": "global", "targets": [], "patterns": ["^Last configuration change.*"]},
    ]
    reduced_rules = [original_rules[0]]

    with sqlite_web_env() as session:
        crud.set_setting(session, key="diff_ignore_rules", value=json.dumps(original_rules, ensure_ascii=False))

    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["diff_rules.delete"])

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.post(
            "/diff-rules",
            data={"rules_json": json.dumps(reduced_rules, ensure_ascii=False)},
            follow_redirects=False,
        )

    assert response.status_code == 303

    with sqlite_web_env() as session:
        saved = json.loads(crud.get_setting(session, key="diff_ignore_rules") or "[]")

    assert saved == reduced_rules


def test_diff_rules_delete_permission_cannot_modify_rule_content(sqlite_web_env):
    original_rules = [
        {"scope": "global", "targets": [], "patterns": ["^ntp clock-period.*"]},
    ]
    modified_rules = [
        {"scope": "global", "targets": [], "patterns": ["^uptime.*"]},
    ]

    with sqlite_web_env() as session:
        crud.set_setting(session, key="diff_ignore_rules", value=json.dumps(original_rules, ensure_ascii=False))

    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["diff_rules.delete"])

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.post(
            "/diff-rules",
            data={"rules_json": json.dumps(modified_rules, ensure_ascii=False)},
            follow_redirects=False,
        )

    assert response.status_code == 403


def test_diff_rules_update_permission_cannot_delete_rules(sqlite_web_env):
    original_rules = [
        {"scope": "global", "targets": [], "patterns": ["^ntp clock-period.*"]},
        {"scope": "global", "targets": [], "patterns": ["^Last configuration change.*"]},
    ]
    reduced_rules = [original_rules[0]]

    with sqlite_web_env() as session:
        crud.set_setting(session, key="diff_ignore_rules", value=json.dumps(original_rules, ensure_ascii=False))

    user_id = _create_user_with_permissions(sqlite_web_env, permission_codes=["diff_rules.update"])

    with TestClient(app) as client:
        _login_client_as_user(client, user_id=user_id)
        response = client.post(
            "/diff-rules",
            data={"rules_json": json.dumps(reduced_rules, ensure_ascii=False)},
            follow_redirects=False,
        )

    assert response.status_code == 403
