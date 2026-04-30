from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import crud
from app import db as app_db
from app.main import app
import app.main as app_main
from app.models import ApiKey
import app.routers.public_api.v1.backups as public_backups
import app.routers.public_api.v1.credentials as public_credentials
import app.routers.public_api.v1.devices as public_devices
import app.routers.public_api.v1.groups as public_groups
import app.routers.public_api.v1.stats as public_stats
import app.routers.public_api.v1.templates as public_templates
from app.services.apikey import generate_api_key


@pytest.fixture()
def sqlite_test_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test_public_api.sqlite"
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

    monkeypatch.setattr(public_stats, "session_scope", test_session_scope)
    monkeypatch.setattr(public_groups, "session_scope", test_session_scope)
    monkeypatch.setattr(public_credentials, "session_scope", test_session_scope)
    monkeypatch.setattr(public_templates, "session_scope", test_session_scope)
    monkeypatch.setattr(public_devices, "session_scope", test_session_scope)
    monkeypatch.setattr(public_backups, "session_scope", test_session_scope)

    test_init_db()
    with test_session_scope() as session:
        crud.ensure_default_roles(session)

    return test_session_scope


@pytest.fixture()
def readonly_api_key(sqlite_test_env):
    username = f"codex_readonly_{uuid4().hex[:12]}"
    user_id, api_key_id, plaintext_key = _create_api_key_for_role(sqlite_test_env, username=username, role="readonly")

    try:
        yield plaintext_key
    finally:
        with sqlite_test_env() as session:
            crud.delete_api_key(session, api_key_id)
            crud.delete_user(session, user_id)


@pytest.fixture()
def admin_api_key(sqlite_test_env):
    username = f"codex_admin_{uuid4().hex[:12]}"
    user_id, api_key_id, plaintext_key = _create_api_key_for_role(sqlite_test_env, username=username, role="admin")

    try:
        yield plaintext_key
    finally:
        with sqlite_test_env() as session:
            crud.delete_api_key(session, api_key_id)
            crud.delete_user(session, user_id)


def _create_api_key_for_role(sqlite_test_env, *, username: str, role: str) -> tuple[int, int, str]:
    plaintext_key, key_hash, prefix = generate_api_key()

    with sqlite_test_env() as session:
        user = crud.create_user(
            session,
            username=username,
            password="TempPass123!",
            role=role,
        )
        api_key = crud.create_api_key(
            session,
            api_key=ApiKey(
                name=f"{username}_key",
                key_hash=key_hash,
                prefix=prefix,
                is_active=True,
                scopes="all",
                created_by=int(user.id),
            ),
        )
        return int(user.id), int(api_key.id), plaintext_key


def test_docs_page_available(sqlite_test_env):
    with TestClient(app) as client:
        response = client.get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_public_api_requires_api_key(sqlite_test_env):
    with TestClient(app) as client:
        response = client.get("/api/v1/stats")
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized", "code": "UNAUTHORIZED"}


def test_readonly_api_key_can_read_public_api(sqlite_test_env, readonly_api_key: str):
    with TestClient(app) as client:
        response = client.get("/api/v1/stats", headers={"X-API-Key": readonly_api_key})
    assert response.status_code == 200
    payload = response.json()
    assert "total_devices" in payload
    assert "unreachable_devices" in payload
    assert "failed_backups_24h" in payload


def test_readonly_api_key_cannot_write_public_api(sqlite_test_env, readonly_api_key: str):
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/groups",
            headers={"X-API-Key": readonly_api_key},
            json={"name": f"forbidden-{uuid4().hex[:8]}"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_public_api_not_found_error_has_code(sqlite_test_env, readonly_api_key: str):
    with TestClient(app) as client:
        response = client.get("/api/v1/groups/999999", headers={"X-API-Key": readonly_api_key})
    assert response.status_code == 404
    assert response.json() == {
        "detail": "Group not found",
        "code": "GROUP_NOT_FOUND",
    }


def test_admin_api_key_crud_flow_and_backup_reads(sqlite_test_env, admin_api_key: str):
    headers = {"X-API-Key": admin_api_key}

    with TestClient(app) as client:
        group_response = client.post(
            "/api/v1/groups",
            headers=headers,
            json={"name": f"core-{uuid4().hex[:8]}"},
        )
        assert group_response.status_code == 201
        group_payload = group_response.json()
        group_id = group_payload["id"]

        credential_response = client.post(
            "/api/v1/credentials",
            headers=headers,
            json={
                "name": f"ops-{uuid4().hex[:8]}",
                "username": "netops",
                "password": "Secret123!",
                "enable_password": "Enable123!",
                "remarks": "seed credential",
            },
        )
        assert credential_response.status_code == 201
        credential_payload = credential_response.json()
        credential_id = credential_payload["id"]

        template_response = client.post(
            "/api/v1/templates",
            headers=headers,
            json={
                "name": f"ios-{uuid4().hex[:8]}",
                "platform": "cisco_ios",
                "commands": "show running-config",
            },
        )
        assert template_response.status_code == 201
        template_payload = template_response.json()
        template_id = template_payload["id"]

        device_response = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "name": f"edge-{uuid4().hex[:8]}",
                "host": f"10.0.{uuid4().int % 200}.{uuid4().int % 200}",
                "port": 22,
                "login_method": "ssh",
                "encoding": "utf-8",
                "platform": "cisco_ios",
                "group_id": group_id,
                "credential_id": credential_id,
                "default_template_id": template_id,
            },
        )
        assert device_response.status_code == 201
        device_payload = device_response.json()
        device_id = device_payload["id"]

        list_response = client.get("/api/v1/devices", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()["total"] >= 1

        update_response = client.put(
            f"/api/v1/devices/{device_id}",
            headers=headers,
            json={
                "name": f"{device_payload['name']}-updated",
                "host": f"10.1.{uuid4().int % 200}.{uuid4().int % 200}",
                "port": 2222,
                "encoding": "gbk",
            },
        )
        assert update_response.status_code == 200
        updated_device = update_response.json()
        assert updated_device["port"] == 2222
        assert updated_device["encoding"] == "gbk"

    with sqlite_test_env() as session:
        record = crud.create_backup_record(
            session,
            device_id=device_id,
            template_id=template_id,
        )
        crud.finish_backup_record(
            session,
            record_id=record.id,
            success=True,
            config_text="hostname edge-test\ninterface Loopback0",
            error_message=None,
            duration_seconds=1.25,
        )
        backup_id = str(record.id)

    with TestClient(app) as client:
        device_backups_response = client.get(f"/api/v1/devices/{device_id}/backups", headers=headers)
        assert device_backups_response.status_code == 200
        backups_payload = device_backups_response.json()
        assert backups_payload["total"] == 1
        assert backups_payload["items"][0]["id"] == backup_id
        assert backups_payload["items"][0]["success"] is True

        backup_content_response = client.get(f"/api/v1/backups/{backup_id}/content", headers=headers)
        assert backup_content_response.status_code == 200
        assert backup_content_response.json() == {
            "config_text": "hostname edge-test\ninterface Loopback0",
        }

        delete_device_response = client.delete(f"/api/v1/devices/{device_id}", headers=headers)
        assert delete_device_response.status_code == 200
        assert delete_device_response.json() == {"status": "success"}

        delete_template_response = client.delete(f"/api/v1/templates/{template_id}", headers=headers)
        assert delete_template_response.status_code == 200
        assert delete_template_response.json() == {"status": "success"}

        delete_credential_response = client.delete(f"/api/v1/credentials/{credential_id}", headers=headers)
        assert delete_credential_response.status_code == 200
        assert delete_credential_response.json() == {"status": "success"}

        delete_group_response = client.delete(f"/api/v1/groups/{group_id}", headers=headers)
        assert delete_group_response.status_code == 200
        assert delete_group_response.json() == {"status": "success"}
