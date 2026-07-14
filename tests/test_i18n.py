from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import crud
from app.core.settings import settings
from app.i18n import (
    get_current_locale,
    get_messages,
    locale_capabilities,
    reset_current_locale,
    set_current_locale,
    translate,
    validate_catalogs,
)
from app.i18n.catalog import CatalogValidationError, _placeholders
from app.i18n.middleware import i18n_http_middleware, resolve_request_locale
from app.i18n.openapi import build_openapi_schema
from app.i18n.render import is_frontend_message_key, javascript_messages
from app.i18n.validators import locale_from_accept_language, normalize_locale, validate_locale
from app.main import _api_error_json, app as main_app
from app.services.audit_service import translate_audit_action, translate_audit_resource, translate_login_fail_reason
from app.models import BackupRecord, BackupSchedule, BackupScheduleRun, Device, TaskEvent
from app.routers.web_context import _layout_context, templates
from app.services import alert_service, schedule_service, task_realtime_service, task_state_service
from app.services.backup_error_service import localize_backup_error_message


def test_catalog_translation_params_and_fallback():
    assert translate("en-US", "button.save") == "Save"
    assert translate("zh-CN", "button.save") == "保存"
    assert translate("en-US", "auth.login.mfa_hint") == (
        "If MFA is enabled, verification will continue after sign-in"
    )
    assert translate("en-US", "auth.mfa.enter_authenticator_code") == (
        "Enter the 6-digit code from your authenticator"
    )
    assert translate("zh-CN", "auth.login.mfa_hint") == "若已启用 MFA，将在登录后继续验证"
    assert translate("zh-CN", "auth.mfa.enter_authenticator_code") == "请输入验证器中的 6 位验证码"
    delete_messages = {
        "dialog.delete.group": "Delete this group?",
        "dialog.delete.template": "Delete this template?",
        "dialog.delete.ignore_rule": "Permanently remove this ignore rule?",
        "dialog.delete.schedule": "Delete this schedule?",
        "dialog.delete.user": "Delete this user?",
        "dialog.delete.role": "Delete this role?",
    }
    for key, expected in delete_messages.items():
        assert translate("en-US", key) == expected
        assert translate("zh-CN", key) != key
    assert translate("en-US", "missing", {"name": "Alice"}, "Hello {name}") == "Hello Alice"
    assert translate("en-US", "missing") == "missing"


def test_locale_normalization_and_validation():
    assert normalize_locale("zh") == "zh-CN"
    assert normalize_locale("en") == "en-US"
    assert validate_locale("en-us") == "en-US"
    assert validate_locale("EN_us") == "en-US"
    try:
        validate_locale("fr-FR")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported locale must be rejected")


def test_catalogs_have_matching_keys_and_placeholders():
    validate_catalogs()


def test_catalogs_are_read_only_and_placeholders_are_simple_identifiers():
    messages = get_messages("en-US")
    with pytest.raises(TypeError):
        messages["button.save"] = "Changed"
    assert _placeholders("Saved {count} items for {name}") == {"count", "name"}
    with pytest.raises(CatalogValidationError):
        _placeholders("Hello {user.name}")
    with pytest.raises(CatalogValidationError):
        _placeholders("Value {count:03d}")


def test_locale_capabilities_support_non_binary_locale_behavior(monkeypatch):
    monkeypatch.setattr(settings, "supported_locales", "zh-CN,en-US,fr-FR,ar-SA")
    french = locale_capabilities("fr-FR")
    arabic = locale_capabilities("ar-SA")
    assert french.language == "fr"
    assert french.cron_locale == "fr"
    assert french.echarts_locale == "fr"
    assert not french.uses_han
    assert arabic.direction == "rtl"


def test_authored_source_has_no_binary_locale_branches_or_generated_keys():
    root = Path(__file__).resolve().parents[1]
    generated_key = re.compile(r"_[0-9a-f]{8}(?:\b|\.)|(?:div_class|span_class|tr_td|tr_class|button_class)")
    binary_locale = re.compile(r"\bisEnglish\b|(?:==|!=|===|!==)\s*['\"]en-US['\"]")
    offenders = []
    for path in (root / "app").rglob("*"):
        if path.suffix not in {".py", ".html", ".js", ".json"} or path.name.endswith(".min.js"):
            continue
        source = path.read_text(encoding="utf-8-sig")
        if generated_key.search(source) or binary_locale.search(source):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_all_templates_compile_and_static_translation_keys_exist():
    root = Path(__file__).resolve().parents[1]
    catalog = get_messages("en-US")
    static_key = re.compile(r"\b_\(\s*(['\"])([A-Za-z0-9_.-]+)\1(?=\s*[,\)])")
    missing: dict[str, list[str]] = {}
    for path in (root / "app" / "templates").rglob("*.html"):
        templates.env.get_template(str(path.relative_to(root / "app" / "templates")).replace("\\", "/"))
        for match in static_key.finditer(path.read_text(encoding="utf-8-sig")):
            key = match.group(2)
            if key not in catalog:
                missing.setdefault(str(path.relative_to(root)), []).append(key)
    assert missing == {}


def test_frontend_source_references_match_explicit_message_contract():
    root = Path(__file__).resolve().parents[1]
    catalog = get_messages("en-US")
    patterns = (
        re.compile(r"(?:window\.)?NB\.t\(\s*(['\"])([^'\"]+)\1"),
        re.compile(r"data-(?:confirm|i18n)-key\s*=\s*(['\"])([^'\"]+)\1"),
    )
    invalid: dict[str, list[str]] = {}
    for base in (root / "app" / "static" / "js", root / "app" / "templates"):
        for path in base.rglob("*"):
            if path.suffix not in {".js", ".html"} or path.name.endswith(".min.js"):
                continue
            source = path.read_text(encoding="utf-8-sig")
            for pattern in patterns:
                for match in pattern.finditer(source):
                    key = match.group(2)
                    if key not in catalog or not is_frontend_message_key(key):
                        invalid.setdefault(str(path.relative_to(root)), []).append(key)
    assert invalid == {}


def test_redirect_error_messages_use_catalog_keys():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "routers" / "web" / "auth.py").read_text(encoding="utf-8-sig")
    assert not re.search(r"[?&](?:err|msg)=[^\"']*[\u3400-\u9fff]", source)


def test_legacy_i18n_cannot_be_reintroduced():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "app" / "i18n" / "legacy.py").exists()
    banned = re.compile(
        r"translate_legacy_text|LegacyI18nExtension|NB_LEGACY_MESSAGES|"
        r"translateLegacy|NB\.tr(?:Html)?\b|__legacy|legacy_javascript_messages"
    )
    offenders = []
    for base in (root / "app",):
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".html", ".js"} or path.name.endswith(".min.js"):
                continue
            if banned.search(path.read_text(encoding="utf-8-sig")):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_english_catalog_has_no_chinese_fallback_text():
    root = Path(__file__).resolve().parents[1]
    messages = json.loads((root / "app" / "i18n" / "locales" / "en-US.json").read_text(encoding="utf-8-sig"))
    offenders = {key: value for key, value in messages.items() if re.search(r"[\u4e00-\u9fff]", value)}
    assert offenders == {}


def test_browser_catalog_contains_only_explicit_frontend_contract():
    browser_messages = javascript_messages("en-US")
    assert "js.nb_common.processing" in browser_messages
    assert "dialog.delete.group" in browser_messages
    assert browser_messages["dialog.delete.title"] == "Confirm deletion"
    assert browser_messages["dialog.delete.confirm"] == "Confirm deletion"
    assert browser_messages["webshell.status.connected"] == "Connected"
    assert "openapi.description" not in browser_messages
    assert len(browser_messages) < 400


def test_accept_language_quality_and_exclusions():
    assert locale_from_accept_language("zh-CN;q=0,en-US;q=0.5") == "en-US"
    assert locale_from_accept_language("en-US;level=1;q=0.2,zh;q=0.8") == "zh-CN"
    assert locale_from_accept_language("fr-FR;q=1,*;q=0.5") == "zh-CN"
    assert locale_from_accept_language("en-US;q=0") is None


def test_request_locale_priority():
    app = FastAPI()

    @app.get("/")
    def endpoint(request: Request):
        locale, persist = resolve_request_locale(request)
        return {"locale": locale, "persist": persist}

    client = TestClient(app)
    assert client.get("/", headers={"Accept-Language": "en-US,en;q=0.8"}).json()["locale"] == "en-US"
    response = client.get("/?lang=en")
    assert response.json() == {"locale": "en-US", "persist": True}

    @app.get("/user")
    def user_endpoint(request: Request):
        locale, persist = resolve_request_locale(request, user=type("User", (), {"locale": "zh-CN"})())
        return {"locale": locale, "persist": persist}

    assert client.get("/user?lang=en-US").json() == {"locale": "en-US", "persist": False}

    @app.get("/api/v1/language")
    def api_endpoint(request: Request):
        locale, persist = resolve_request_locale(
            request,
            user=type("User", (), {"locale": "zh-CN"})(),
        )
        return {"locale": locale, "persist": persist}

    assert client.get(
        "/api/v1/language",
        headers={"Accept-Language": "en-US"},
    ).json() == {"locale": "en-US", "persist": False}

    client.cookies.set("nb_locale", "unsupported")
    assert client.get(
        "/",
        headers={"Accept-Language": "en-US"},
    ).json() == {"locale": "en-US", "persist": False}


def test_middleware_sets_context_header_and_cookie():
    app = FastAPI()
    app.middleware("http")(i18n_http_middleware)

    @app.get("/")
    def endpoint():
        return {"locale": get_current_locale()}

    client = TestClient(app)
    response = client.get("/?lang=en-US")
    assert response.json() == {"locale": "en-US"}
    assert response.headers["Content-Language"] == "en-US"
    assert "Accept-Language" in response.headers["Vary"]
    assert "Cookie" in response.headers["Vary"]
    assert response.cookies["nb_locale"] == "en-US"
    assert response.cookies.get("nb_locale") == "en-US"
    assert "Max-Age=31536000" in response.headers["Set-Cookie"]
    assert "HttpOnly" in response.headers["Set-Cookie"]


def test_api_error_keeps_code_and_localizes_message():
    token = set_current_locale("en-US")
    try:
        response = _api_error_json(status_code=401, code="UNAUTHORIZED", message="Unauthorized")
    finally:
        reset_current_locale(token)
    assert b'"code":"UNAUTHORIZED"' in response.body
    assert b'"message":"Unauthorized or session expired"' in response.body


def test_api_error_catalog_message_is_localized():
    token = set_current_locale("en-US")
    try:
        response = _api_error_json(status_code=404, code="DEVICE_NOT_FOUND", message="设备不存在")
    finally:
        reset_current_locale(token)
    assert b'"code":"DEVICE_NOT_FOUND"' in response.body
    assert b'"message":"Device not found"' in response.body


def test_openapi_and_audit_translation():
    schema = build_openapi_schema(main_app, "en-US")
    assert schema["info"]["title"] == "EasyNetBak API"
    assert "API documentation" in schema["info"]["description"]
    assert translate_audit_action("CREATE_DEVICE", "en-US") == "Create device"
    assert translate_audit_resource("device", "en-US") == "Device"
    assert translate_login_fail_reason("invalid_mfa", "en-US") == "Invalid verification code"


def test_explicit_template_keys_and_statuses_are_localized():
    template = templates.env.from_string('<button>{{ _("button.cancel") }}</button><h1>{{ _("nav.devices") }}</h1>')
    assert template.render(locale="en-US") == "<button>Cancel</button><h1>Devices</h1>"
    assert template.render(locale="zh-CN") == "<button>取消</button><h1>设备管理</h1>"
    assert task_state_service.get_backup_record_status_label("running", "en-US") == "Running"
    assert task_state_service.get_schedule_run_status_label("partial_failed", "en-US") == "Partially failed"


def test_navigation_subtitles_and_device_statuses_are_localized():
    assert translate("en-US", "nav.devices") == "Devices"
    assert translate("en-US", "page.devices.subtitle").startswith("Manage device inventory")
    assert translate("en-US", "dashboard.window.7d") == "Last 7 days"
    assert translate("en-US", "status.device.online") == "Online"
    assert translate("en-US", "status.device.authentication_failed") == "Authentication failed"
    assert translate("zh-CN", "status.device.offline") == "离线"
    assert translate("zh-CN", "status.backup.running") == "运行中"
    assert translate("zh-CN", "status.schedule_run.partial_failed") == "部分失败"


def test_dashboard_heatmap_uses_translatable_range_keys(monkeypatch):
    monkeypatch.setattr(crud, "_list_config_change_timestamps", lambda *args, **kwargs: [])
    for window_key in ("24h", "7d", "30d"):
        payload = crud.get_config_change_heatmap_stats(None, window_key=window_key)
        assert translate("en-US", payload["range_label"]).startswith("Last ")
        for label in payload.get("y_labels", []):
            assert not re.search(r"[\u4e00-\u9fff]", translate("en-US", label, fallback=label))


def test_flash_message_key_and_params_are_localized_server_side():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/devices",
            "query_string": b"msg=message.devices_updated&count=4",
            "headers": [],
        }
    )
    request.state.locale = "en-US"
    context = _layout_context(request=request, active="devices")
    assert context["flash_message"] == "4 devices updated"
    assert context["role_labels"]["admin"] == "System administrator"
    assert context["role_labels"]["operator"] == "Operator"
    assert context["role_labels"]["readonly"] == "Read-only user"


def test_users_list_uses_localized_role_labels():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "templates" / "users.html").read_text(encoding="utf-8-sig")
    assert "role_labels.get(u.role, stored_role_label)" in source


def test_schedule_next_run_states_are_localized():
    disabled = BackupSchedule(id=1, name="disabled", crontab="0 2 * * *", enabled=False, targets="all")
    payload = schedule_service._build_next_run_payload(
        [disabled],
        timezone_offset="+08:00",
        locale="en-US",
    )
    assert payload[1] == {"text": "Disabled", "tone": "secondary"}


def test_webshell_status_line_has_no_escaped_newline_prefix():
    line = translate(
        "en-US",
        "js.webshell.status_line",
        {"value0": "System", "value1": "Connected"},
    )
    assert line == "[System] Connected"
    assert "\\r\\n" not in line


def test_schedule_error_summary_is_localized_for_english():
    summary = schedule_service.summarize_schedule_run_error(
        '{"termination_mode":"pending_only","cancelled_backups":3,"unfinished_backups":2,'
        '"failures_by_type":{"TIMEOUT":1,"UNKNOWN":2}}',
        locale="en-US",
    )
    assert summary == (
        "Cancelled 3 pending tasks; 2 tasks are still running; "
        "Failure types: Timed out: 1, Unknown error: 2"
    )
    assert not re.search(r"[\u4e00-\u9fff]", summary)


def test_status_badge_macro_localizes_labels():
    template = templates.env.from_string(
        '{% from "macros.html" import render_backup_record_status with context %}'
        '{{ render_backup_record_status("failed") }}'
        '{{ render_backup_record_status("succeeded") }}'
    )
    html = template.render(locale="en-US")
    assert "Failed" in html
    assert "Succeeded" in html
    assert "失败" not in html
    assert "成功" not in html


def test_task_events_return_localized_message_and_stable_payload():
    event = TaskEvent(event="backup_record_command_started", details='{"command":"show running-config"}')
    item = task_realtime_service._serialize_task_event(event, offset_minutes=0, locale="en-US")
    assert item["message"] == "Running command: show running-config"
    assert item["message_key"] == "task.event.backup_record_command_started"
    assert item["message_params"] == {"command": "show running-config"}


def test_task_events_keep_operational_details_after_localization():
    collection = TaskEvent(
        event="backup_record_collection_completed",
        details='{"line_count":192,"content_bytes":6144}',
    )
    collection_zh = task_realtime_service._serialize_task_event(collection, offset_minutes=0, locale="zh-CN")
    collection_en = task_realtime_service._serialize_task_event(collection, offset_minutes=0, locale="en-US")
    assert collection_zh["message"] == "配置采集完成，共 192 行"
    assert collection_en["message"] == "Configuration collection completed; 192 lines"

    upload = TaskEvent(
        event="backup_record_storage_upload_started",
        storage_type="FTP",
        details=(
            '{"storage_type":"FTP","host":"192.168.225.1","port":"21",'
            '"base_dir":"网络设备配置","passive":"1","content_bytes":6144}'
        ),
    )
    upload_zh = task_realtime_service._serialize_task_event(upload, offset_minutes=0, locale="zh-CN")
    assert upload_zh["message"] == (
        "开始上传到 FTP: 192.168.225.1:21，目录 /网络设备配置，被动模式 1，数据 6.0 KB"
    )

    planned = TaskEvent(
        event="schedule_run_planned",
        details='{"planned_count":31,"schedule_id":7,"trigger":"manual"}',
    )
    planned_zh = task_realtime_service._serialize_task_event(planned, offset_minutes=0, locale="zh-CN")
    planned_en = task_realtime_service._serialize_task_event(planned, offset_minutes=0, locale="en-US")
    assert planned_zh["message"] == "批次已创建，计划备份 31 台设备，计划 ID 7，触发方式 manual"
    assert planned_en["message"] == (
        "Batch created; 31 device backups planned; schedule ID: 7; trigger: manual"
    )


def test_backup_execution_log_messages_are_fully_localized():
    cases = [
        (
            "backup_record_connection_started",
            {"host": "192.168.225.5", "port": 22, "login_method": "ssh"},
            "Connecting to 192.168.225.5:22 (ssh)",
        ),
        (
            "backup_record_netmiko_connecting",
            {"host": "192.168.225.5", "port": 22, "device_type": "h3c_comware", "conn_timeout": 30},
            "Opening Netmiko session to 192.168.225.5:22; driver: h3c_comware; connection timeout: 30s",
        ),
        (
            "backup_record_command_started",
            {"command": "display clock", "command_index": 1, "command_count": 4, "read_timeout": 240},
            "Running command: [1/4] display clock; read timeout: 240s",
        ),
        (
            "backup_record_command_completed",
            {
                "command": "display clock",
                "command_index": 1,
                "command_count": 4,
                "line_count": 3,
                "content_bytes": 87,
                "duration_seconds": 0.075,
            },
            "Command completed [1/4] display clock; output: 3 lines / 87 B; duration: 0.075s",
        ),
    ]
    for event_name, details, expected in cases:
        event = TaskEvent(event=event_name, details=json.dumps(details))
        item = task_realtime_service._serialize_task_event(event, offset_minutes=0, locale="en-US")
        assert item["message"] == expected
        assert not any("\u4e00" <= char <= "\u9fff" for char in item["message"])


def test_backup_error_message_is_fully_localized_and_keeps_technical_detail():
    raw = (
        "连接超时: 设备不可达或端口不通 "
        "(TCP connection to device failed. Common causes include an incorrect hostname or IP address.)"
    )
    localized = localize_backup_error_message(raw, "TIMEOUT", locale="en-US")
    assert localized == (
        "Connection timed out: the device is unreachable or the port is unavailable "
        "(TCP connection to device failed. Common causes include an incorrect hostname or IP address.)"
    )
    assert not any("\u4e00" <= char <= "\u9fff" for char in localized)
    assert localize_backup_error_message(raw, "TIMEOUT", locale="zh-CN") == raw


def test_backup_error_message_leaves_already_english_errors_unchanged():
    raw = "Authentication failure: invalid credentials"
    assert localize_backup_error_message(raw, "AUTH_FAILED", locale="en-US") == raw


def test_batch_email_uses_finalizer_snapshot_for_failure_and_change_lists(monkeypatch):
    run = BackupScheduleRun(schedule_id=1, total_devices=2, success_count=1, fail_count=1)
    failed_device = Device(id=1, name="failed-device", host="192.0.2.10", platform="cisco_ios")
    changed_device = Device(id=2, name="changed-device", host="192.0.2.20", platform="cisco_ios")
    failed_record = BackupRecord(
        device_id=1,
        status=task_state_service.BACKUP_RECORD_STATUS_FAILED,
        success=False,
        error_message=(
            "连接超时: 设备不可达或端口不通 "
            "(TCP connection to device failed. Wrong TCP port.)"
        ),
        failure_type="TIMEOUT",
        duration_seconds=21.04,
    )
    changed_record = BackupRecord(
        device_id=2,
        status=task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED,
        success=True,
        config_text="hostname changed-device",
    )
    previous_record = BackupRecord(
        device_id=2,
        status=task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED,
        success=True,
        config_text="hostname old-device",
    )
    devices = {1: failed_device, 2: changed_device}
    settings = {
        "always_send_summary": "1",
        "alert_on_fail": "1",
        "alert_on_config_change": "1",
        "timezone_offset": "+00:00",
    }
    captured: dict[str, str] = {}

    monkeypatch.setattr(crud, "get_schedule_run", lambda session, run_id: run)
    monkeypatch.setattr(crud, "get_setting", lambda session, key: settings.get(key))
    monkeypatch.setattr(crud, "get_device", lambda session, device_id: devices.get(device_id))
    monkeypatch.setattr(
        crud,
        "list_schedule_run_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot path must not re-query run items")),
    )
    monkeypatch.setattr(crud, "list_device_backups", lambda session, device_id, limit=2: [changed_record, previous_record])
    monkeypatch.setattr(
        alert_service,
        "_config_change_summary_after_diff_rules",
        lambda *args, **kwargs: {
            "changed": True,
            "context_lines": 3,
            "sample_limit": 1,
            "total_sample_rows": 1,
            "sample_lines": [{"prefix": "+", "text": "hostname changed-device", "kind": "add"}],
        },
    )
    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": captured.update(subject=subject, content=content) or True,
    )

    result = alert_service.check_and_alert_batch(
        None,
        run.id,
        records=[failed_record, changed_record],
    )

    assert result["email_sent"] is True
    assert result["failed_count"] == 1
    assert result["changed_count"] == 1
    assert "失败列表" in captured["content"]
    assert "failed-device" in captured["content"]
    assert "耗时" in captured["content"]
    assert "21.04s" in captured["content"]
    assert "错误类型" in captured["content"]
    assert "TIMEOUT" in captured["content"]
    assert "连接超时: 设备不可达或端口不通" in captured["content"]
    assert "配置变更列表" in captured["content"]
    assert "changed-device" in captured["content"]
    assert "hostname changed-device" in captured["content"]

    failed_record.locale = "en-US"
    captured.clear()
    english_result = alert_service.check_and_alert_batch(
        None,
        run.id,
        records=[failed_record, changed_record],
    )
    assert english_result["email_sent"] is True
    assert "Duration" in captured["content"]
    assert "Failure type" in captured["content"]
    assert "Error details" in captured["content"]
    assert "Connection timed out: the device is unreachable or the port is unavailable" in captured["content"]
    assert "TCP connection to device failed. Wrong TCP port." in captured["content"]
    assert "连接超时" not in captured["content"]
