from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app import crud
from app.core.settings import settings
from app.i18n import (
    get_current_locale,
    get_messages,
    locale_capabilities,
    reset_current_locale,
    set_current_locale,
    translate,
    translate_plural,
    validate_catalogs,
)
from app.i18n import catalog as catalog_module
from app.i18n.catalog import CatalogValidationError, _load_catalog_file, _placeholders
from app.i18n.middleware import i18n_http_middleware, resolve_request_locale
from app.i18n.openapi import build_openapi_schema
from app.i18n.render import is_frontend_message_key, javascript_messages
from app.i18n.validators import locale_from_accept_language, normalize_locale, validate_locale
from app.main import _api_error_json, app as main_app
from app.routers.web.auth import _safe_next_url
from app.services.audit_service import translate_audit_action, translate_audit_resource, translate_login_fail_reason
from app.models import (
    BackupRecord,
    BackupSchedule,
    BackupScheduleRun,
    Device,
    NotificationChannel,
    NotificationDelivery,
    NotificationEvent,
    NotificationPolicy,
    NotificationTemplate,
    TaskEvent,
)
from app.routers.web_context import _layout_context, templates
from app.services import (
    alert_service,
    backup_service,
    ftp_service,
    notification_routing_service,
    s3_service,
    schedule_service,
    task_realtime_service,
    task_state_service,
)
from app.services.backup_error_service import localize_backup_error_message


def test_backup_command_sections_split_single_command_and_keep_prompt():
    assert backup_service.split_backup_command_output(
        "switch-01#show running-config\ninterface Vlan1\n ip address 10.0.0.1\n",
        ["show running-config"],
    ) == [
        {
            "command": "show running-config",
            "output": "switch-01#show running-config\ninterface Vlan1\n ip address 10.0.0.1",
        }
    ]


def test_backup_command_sections_use_ordered_prompt_boundaries():
    sections = backup_service.split_backup_command_output(
        "Total: 2\n\nedge-sw#show running-config\nhostname edge-sw\ninterface Vlan1\n",
        ["show mac-address", "show running-config"],
    )

    assert sections == [
        {"command": "show mac-address", "output": "Total: 2"},
        {
            "command": "show running-config",
            "output": "edge-sw#show running-config\nhostname edge-sw\ninterface Vlan1",
        },
    ]


def test_backup_command_sections_support_huawei_prompts():
    sections = backup_service.split_backup_command_output(
        "<core-sw>display mac-address\nMAC Address  VLAN\n"
        "[core-sw]display current-configuration\nsysname core-sw",
        ["display mac-address", "display current-configuration"],
    )

    assert [section["output"] for section in sections] == [
        "<core-sw>display mac-address\nMAC Address  VLAN",
        "[core-sw]display current-configuration\nsysname core-sw",
    ]


def test_backup_command_sections_refuse_ambiguous_multi_command_output():
    assert backup_service.split_backup_command_output(
        "Total: 2\n\nhostname edge-sw\ninterface Vlan1\n",
        ["show mac-address", "show running-config"],
    ) == []


def test_backup_command_list_uses_last_recorded_execution_attempt():
    session = Mock()
    session.exec.return_value.all.return_value = [
        TaskEvent(details=json.dumps({"command": "show version", "command_index": 1})),
        TaskEvent(details=json.dumps({"command": "show interfaces", "command_index": 2})),
        TaskEvent(details=json.dumps({"command": "show version", "command_index": 1})),
        TaskEvent(details=json.dumps({"command": "show interfaces", "command_index": 2})),
    ]

    assert backup_service._commands_from_backup_events(session, uuid4()) == [
        "show version",
        "show interfaces",
    ]


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


def test_storage_connection_messages_follow_request_locale(monkeypatch):
    class FakeS3Client:
        def put_object(self, **kwargs):
            return None

        def delete_object(self, **kwargs):
            return None

    monkeypatch.setattr(s3_service.boto3, "client", lambda *args, **kwargs: FakeS3Client())
    success, message = s3_service.test_s3_connection(
        endpoint="https://s3.example.test",
        access_key="access-key",
        secret_key="secret-key",
        bucket="backup-bucket",
        region="test-region",
        locale="en-US",
    )
    assert success is True
    assert message == "S3 connection succeeded and write access was verified."

    def refuse_connection(*args, **kwargs):
        raise ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。")

    monkeypatch.setattr(ftp_service, "_ftp_connect", refuse_connection)
    success, message = ftp_service.test_ftp_connection(
        host="192.0.2.1",
        port="21",
        username="admin",
        password="secret",
        base_dir="",
        passive="1",
        timeout="15",
        encoding="utf-8",
        locale="en-US",
    )
    assert success is False
    assert message == (
        "FTP connection failed (path encoding: utf-8): "
        "The target host refused the connection."
    )


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


def test_catalog_loader_rejects_duplicate_keys(monkeypatch, tmp_path):
    (tmp_path / "en-US.json").write_text('{"same":"first","same":"second"}', encoding="utf-8")
    monkeypatch.setattr(catalog_module, "_LOCALES_DIR", tmp_path)
    with pytest.raises(CatalogValidationError, match="duplicate keys"):
        _load_catalog_file("en-US")


def test_strict_interpolation_and_plural_rules():
    assert translate_plural("en-US", "message.devices_updated", 1) == "1 device updated"
    assert translate_plural("en-US", "message.devices_updated", 2) == "2 devices updated"
    assert translate_plural("zh-CN", "message.devices_updated", 2) == "成功更新 2 台设备"
    with pytest.raises(CatalogValidationError, match=r"missing=\['count'\]"):
        translate("en-US", "message.devices_updated", strict=True)
    assert translate("en-US", "button.save", {"unused": 1}, strict=True) == "Save"


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


def test_global_toast_layer_stays_above_drawers_and_bypasses_stale_css():
    root = Path(__file__).resolve().parents[1]
    template_source = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8-sig")
    css_source = (root / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8-sig")

    assert re.search(r'href="/static/css/app\.css\?v=\{\{ app_version \}\}[^\"]*"', template_source)
    assert re.search(
        r'class="toast-container nb-toast-layer[^\"]*"\s+style="z-index:\s*11000;"',
        template_source,
    )

    toast_layer_rule = re.search(
        r"\.toast-container\.nb-toast-layer\s*\{[^}]*z-index:\s*11000\s*!important;?[^}]*\}",
        css_source,
        re.DOTALL,
    )
    assert toast_layer_rule is not None
    assert toast_layer_rule.start() > css_source.rfind(".toast-container {")
    assert toast_layer_rule.start() > css_source.rfind(".offcanvas.show")


def test_backup_view_uses_one_styled_scroll_container():
    root = Path(__file__).resolve().parents[1]
    css_source = (root / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8-sig")

    assert re.search(
        r"#backup-view-modal \.backup-command-output\s*\{[^}]*overflow:\s*auto;",
        css_source,
        re.DOTALL,
    )
    assert re.search(
        r"#backup-view-modal \.backup-command-output > \.config-view-pre\s*\{"
        r"[^}]*max-height:\s*none;[^}]*overflow:\s*visible;",
        css_source,
        re.DOTALL,
    )


def test_frontend_source_references_match_explicit_message_contract():
    root = Path(__file__).resolve().parents[1]
    catalog = get_messages("en-US")
    patterns = (
        re.compile(r"(?:window\.)?NB\.t\(\s*(['\"])([^'\"]+)\1"),
        re.compile(r"(?:window\.)?NB\.tp\(\s*(['\"])([^'\"]+)\1"),
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/dashboard"),
        ("", "/dashboard"),
        ("/devices", "/devices"),
        ("/backups?page=2#latest", "/backups?page=2#latest"),
        ("https://evil.example/path", "/dashboard"),
        ("//evil.example/path", "/dashboard"),
        ("///evil.example/path", "/dashboard"),
        (r"\evil.example\path", "/dashboard"),
        (r"/\evil.example/path", "/dashboard"),
        ("%2F%2Fevil.example/path", "/dashboard"),
        ("/%5Cevil.example/path", "/dashboard"),
        ("/dashboard%0d%0aLocation:%20https://evil.example", "/dashboard"),
        ("dashboard", "/dashboard"),
    ],
)
def test_safe_next_url_only_allows_local_paths(value: str | None, expected: str) -> None:
    assert _safe_next_url(value) == expected


def test_safe_next_url_supports_a_custom_default() -> None:
    assert _safe_next_url("https://evil.example", default="/") == "/"


def test_user_visible_message_sinks_do_not_hardcode_chinese():
    root = Path(__file__).resolve().parents[1]
    han = r"[\u3400-\u9fff]"
    sink_patterns = (
        re.compile(rf"(?:RedirectResponse|HTTPException|showToast)\([^\n]*{han}"),
        re.compile(rf"[\"']message[\"']\s*:\s*[^\n]*{han}"),
        re.compile(rf"\bmessage\s*=\s*[^\n]*{han}"),
    )
    offenders: list[str] = []
    checked_paths = [
        *(root / "app" / "routers").rglob("*.py"),
        root / "app" / "services" / "api_key_management_service.py",
        root / "app" / "services" / "backup_service.py",
        root / "app" / "services" / "device_service.py",
        root / "app" / "services" / "ftp_service.py",
        root / "app" / "services" / "s3_service.py",
        root / "app" / "services" / "task_orchestration_service.py",
        root / "app" / "services" / "task_realtime_service.py",
        root / "app" / "services" / "task_runtime_config_service.py",
    ]
    for path in checked_paths:
        source = path.read_text(encoding="utf-8-sig")
        for pattern in sink_patterns:
            if pattern.search(source):
                offenders.append(str(path.relative_to(root)))
                break
    assert offenders == []


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
    # 品牌名等专有名称允许在英文界面保留中文原文
    allowed_chinese_keys = {"template.base.product_tagline"}
    offenders = {
        key: value
        for key, value in messages.items()
        if key not in allowed_chinese_keys and re.search(r"[\u4e00-\u9fff]", value)
    }
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


def test_browser_catalog_is_page_scoped_and_contains_no_markup():
    dashboard_messages = javascript_messages("en-US", page="dashboard")
    schedule_messages = javascript_messages("en-US", page="schedules")
    assert "js.dashboard.change_count" in dashboard_messages
    assert "js.schedules.syncing" not in dashboard_messages
    assert "js.schedules.syncing" in schedule_messages
    assert "webshell.status.connected" in javascript_messages("en-US", page="devices")
    assert dashboard_messages["task.selected_devices.one"] == "{count} device selected"
    assert len(dashboard_messages) < len(javascript_messages("en-US"))
    assert all(not re.search(r"<\s*/?\s*[A-Za-z]", value) for value in schedule_messages.values())


def test_catalog_messages_are_plain_text_and_quality_regressions_are_absent():
    for locale in ("zh-CN", "en-US"):
        messages = get_messages(locale)
        assert not [key for key in messages if key.endswith("_html")]
        assert not [value for value in messages.values() if re.search(r"<\s*/?\s*[A-Za-z]", value)]
    english = get_messages("en-US")
    assert english["template.schedules.noneschedules"] == "No schedules"
    assert english["template.templates.usesystemdefault"] == "Use system default"
    assert english["js.nb_common.devicelogs"] == "Device logs"


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


def test_static_responses_do_not_vary_by_locale(tmp_path):
    (tmp_path / "asset.js").write_text("const ok = true;", encoding="utf-8")
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=tmp_path), name="static")
    app.middleware("http")(i18n_http_middleware)
    response = TestClient(app).get("/static/asset.js", headers={"Accept-Language": "en-US"})
    assert response.status_code == 200
    assert "Content-Language" not in response.headers
    assert "Vary" not in response.headers


def test_model_locale_defaults_follow_configuration(monkeypatch):
    monkeypatch.setattr(settings, "supported_locales", "zh-CN,en-US")
    monkeypatch.setattr(settings, "default_locale", "en-US")
    assert Device(name="device", host="127.0.0.1").name == "device"
    from app.models import User

    assert User(username="user", password_hash="hash").locale == "en-US"
    assert BackupRecord(device_id=1).locale == "en-US"


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
    public_summaries = [
        operation["summary"]
        for path, path_item in schema["paths"].items()
        if path.startswith("/api/v1/")
        for operation in path_item.values()
        if isinstance(operation, dict) and "summary" in operation
    ]
    assert "List devices" in public_summaries
    assert not [summary for summary in public_summaries if summary.startswith("openapi.")]
    assert not [summary for summary in public_summaries if re.search(r"[\u4e00-\u9fff]", summary)]
    assert translate_audit_action("CREATE_DEVICE", "en-US") == "Create device"
    assert translate_audit_resource("device", "en-US") == "Device"
    assert translate_audit_resource("notification_channel", "zh-CN") == "通知通道"
    assert translate_audit_resource("notification_template", "zh-CN") == "通知模板"
    assert translate_audit_resource("notification_policy", "zh-CN") == "通知策略"
    assert translate_audit_resource("notification_channel", "en-US") == "Notification channel"
    assert translate_audit_resource("notification_template", "en-US") == "Notification template"
    assert translate_audit_resource("notification_policy", "en-US") == "Notification policy"
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


def test_all_supported_task_log_events_have_bilingual_messages():
    source = (Path(__file__).parents[1] / "app" / "services" / "task_realtime_service.py").read_text(
        encoding="utf-8-sig"
    )
    supported_events = set(re.findall(r'event_name == "([a-z0-9_]+)"', source))
    assert supported_events
    for locale in ("zh-CN", "en-US"):
        messages = get_messages(locale)
        missing = sorted(event for event in supported_events if f"task.event.{event}" not in messages)
        assert missing == []


def test_task_log_events_do_not_expose_internal_event_identifiers():
    cases = [
        (
            "backup_record_alert_check_started",
            {},
            "开始检查告警与通知条件",
        ),
        (
            "schedule_run_finalizer_scheduled",
            {"backup_count": 24, "poll_seconds": 5},
            "已安排批次收尾检查，跟踪 24 个任务，每 5 秒检查一次",
        ),
        (
            "schedule_run_alert_check_completed",
            {"success_count": 3, "fail_count": 7},
            "批次汇总通知条件检查完成",
        ),
    ]
    for event_name, details, expected in cases:
        event = TaskEvent(event=event_name, details=json.dumps(details))
        item = task_realtime_service._serialize_task_event(event, offset_minutes=0, locale="zh-CN")
        assert item["message"] == expected
        assert event_name not in item["message"]


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
    monkeypatch.setattr(
        notification_routing_service,
        "is_builtin_policy_enabled",
        lambda session, kind: {
            "failure": True,
            "config_change": True,
            "summary": True,
        }[kind],
    )
    monkeypatch.setattr(notification_routing_service, "has_custom_policy_for_event", lambda *args, **kwargs: False)
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


def test_builtin_notification_policies_use_literal_events_and_match_batch_details(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    values = {
        "smtp_host": "smtp.example.invalid",
        "smtp_port": "587",
        "smtp_user": "backup@example.invalid",
        "smtp_pass": "encrypted-value",
        "smtp_from": "backup@example.invalid",
        "smtp_to": "ops@example.invalid",
        "alert_on_fail": "1",
        "alert_on_config_change": "1",
        "always_send_summary": "1",
    }
    monkeypatch.setattr(crud, "get_setting", lambda session, key: values.get(key))
    sent: list[dict] = []
    monkeypatch.setattr(
        notification_routing_service,
        "_send_channel",
        lambda channel, **kwargs: sent.append(kwargs),
    )

    with Session(engine) as session:
        notification_routing_service.ensure_builtin_defaults(session)
        policies = {
            policy.builtin_key: policy
            for policy in session.exec(select(NotificationPolicy)).all()
            if policy.builtin_key
        }
        assert json.loads(policies["builtin_failure"].event_types_json) == ["backup_failed", "task_cancelled"]
        assert json.loads(policies["builtin_config_change"].event_types_json) == ["config_changed"]
        assert json.loads(policies["builtin_summary"].event_types_json) == ["backup_summary"]
        assert notification_routing_service.has_unconditional_summary_policy(session) is True

        result = notification_routing_service.dispatch_event(
            session,
            event_type="backup_summary",
            source_key="test:literal-batch-events:1",
            locale="zh-CN",
            payload={
                "items": [
                    {"device_name": "edge-failed", "success": False, "cancelled": False},
                    {"device_name": "edge-cancelled", "success": False, "cancelled": True},
                    {"device_name": "edge-changed", "success": True, "cancelled": False, "changed": True},
                    {"device_name": "edge-ok", "success": True, "cancelled": False},
                ],
            },
            fallback_subject="备份批次结果",
            fallback_body="<html><body>batch</body></html>",
        )
        deliveries = session.exec(select(NotificationDelivery)).all()
        routed_by_policy = {
            session.get(NotificationPolicy, delivery.policy_id).builtin_key: json.loads(delivery.payload_json)
            for delivery in deliveries
        }

    assert result["sent"] == 3
    assert len(sent) == 3
    assert [item["device_name"] for item in routed_by_policy["builtin_failure"]["items"]] == [
        "edge-failed",
        "edge-cancelled",
    ]
    assert [item["device_name"] for item in routed_by_policy["builtin_config_change"]["items"]] == [
        "edge-changed"
    ]
    assert len(routed_by_policy["builtin_summary"]["items"]) == 4


def test_notification_delivery_times_use_system_timezone(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        crud,
        "get_setting",
        lambda session, key: "+08:00" if key == "timezone_offset" else None,
    )

    with Session(engine) as session:
        channel = NotificationChannel(name="SMTP", channel_type="smtp", enabled=True)
        event = NotificationEvent(event_type="backup_summary", source_key="test:timezone:1", locale="zh-CN")
        session.add(channel)
        session.add(event)
        session.flush()
        session.add(
            NotificationDelivery(
                event_id=event.id,
                channel_id=channel.id,
                dedupe_key="timezone-delivery",
                status="sent",
                created_at=datetime(2026, 7, 17, 1, 2, 3),
                sent_at=datetime(2026, 7, 17, 1, 3, 4),
            )
        )
        session.flush()

        row = notification_routing_service.list_deliveries(session)[0]

    assert row["created_at"] == "2026-07-17 09:02:03"
    assert row["sent_at"] == "2026-07-17 09:03:04"


def test_notification_delivery_filters_apply_before_pagination(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(crud, "get_setting", lambda session, key: "+00:00" if key == "timezone_offset" else None)

    with Session(engine) as session:
        smtp = NotificationChannel(name="SMTP", channel_type="smtp", enabled=True)
        robot = NotificationChannel(name="Robot", channel_type="wecom", enabled=True)
        failed_event = NotificationEvent(event_type="backup_failed", source_key="test:filters:failed")
        summary_event = NotificationEvent(event_type="backup_summary", source_key="test:filters:summary")
        session.add_all([smtp, robot, failed_event, summary_event])
        session.flush()
        session.add_all([
            NotificationDelivery(
                event_id=failed_event.id,
                channel_id=smtp.id,
                dedupe_key="filter-failed",
                subject="Edge backup failed",
                status="failed",
                last_error="Connection timeout",
                created_at=datetime(2026, 7, 18, 1, 0, 0),
            ),
            NotificationDelivery(
                event_id=summary_event.id,
                channel_id=robot.id,
                dedupe_key="filter-summary",
                subject="Daily summary",
                status="sent",
                created_at=datetime(2026, 7, 19, 1, 0, 0),
            ),
        ])
        session.flush()

        filters = {
            "q": "timeout",
            "status": "failed",
            "channel_id": smtp.id,
            "event_type": "backup_failed",
            "created_from": datetime(2026, 7, 18, 0, 0, 0),
            "created_to": datetime(2026, 7, 19, 0, 0, 0),
        }
        rows = notification_routing_service.list_deliveries(session, limit=1, offset=0, **filters)
        total = notification_routing_service.count_deliveries(session, **filters)

    assert total == 1
    assert [row["subject"] for row in rows] == ["Edge backup failed"]


def test_notification_policy_simulator_reuses_matching_and_stop_processing_rules():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        channel = NotificationChannel(name="Ops SMTP", channel_type="smtp", enabled=True)
        template = NotificationTemplate(
            name="Failure mail",
            event_type="*",
            channel_type="smtp",
            body_template="Failure",
        )
        session.add_all([channel, template])
        session.flush()
        session.add_all([
            NotificationPolicy(
                name="Junos only",
                priority=10,
                event_types_json='["backup_failed"]',
                platforms_json='["junos"]',
                channel_ids_json=f"[{channel.id}]",
                template_id=template.id,
            ),
            NotificationPolicy(
                name="Cisco failures",
                priority=20,
                event_types_json='["backup_failed"]',
                platforms_json='["cisco_ios"]',
                failure_types_json='["TIMEOUT"]',
                channel_ids_json=f"[{channel.id}]",
                template_id=template.id,
                stop_processing=True,
            ),
            NotificationPolicy(
                name="Should not run",
                priority=30,
                event_types_json='["backup_failed"]',
                channel_ids_json=f"[{channel.id}]",
                template_id=template.id,
            ),
        ])
        session.flush()

        result = notification_routing_service.simulate_policy_routes(
            session,
            event_type="backup_failed",
            platform="cisco_ios",
            failure_type="TIMEOUT",
        )

    assert result["route_count"] == 1
    assert result["stopped"] is True
    assert [item["policy_name"] for item in result["matches"]] == ["Cisco failures"]
    assert result["matches"][0]["routes"][0]["channel_name"] == "Ops SMTP"


def test_notification_policy_simulator_always_includes_backup_summary_policy():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        channel = NotificationChannel(name="Summary SMTP", channel_type="smtp", enabled=True)
        template = NotificationTemplate(
            name="Summary mail",
            event_type="*",
            channel_type="smtp",
            body_template="Summary",
        )
        session.add_all([channel, template])
        session.flush()
        session.add(
            NotificationPolicy(
                name="Backup summary report",
                priority=902,
                event_types_json='["backup_summary"]',
                group_ids_json="[0]",
                channel_ids_json=f"[{channel.id}]",
                template_id=template.id,
            )
        )
        session.flush()

        results = {
            event_type: notification_routing_service.simulate_policy_routes(
                session,
                event_type=event_type,
                platform="cisco_ios",
                failure_type="TIMEOUT" if event_type == "backup_failed" else "",
            )
            for event_type in notification_routing_service.EVENT_TYPES
        }

    assert all(result["route_count"] == 1 for result in results.values())
    assert all(
        [item["policy_name"] for item in result["matches"]] == ["Backup summary report"]
        for result in results.values()
    )


def test_builtin_robot_markdown_keeps_lists_without_error_or_change_detail_columns():
    context = notification_routing_service.sample_template_context(
        locale="zh-CN",
        event_type="backup_summary",
    )
    html_body = notification_routing_service.render_custom_template(
        notification_routing_service.DETAILED_BACKUP_BODY_TEMPLATE,
        context,
        content_type="html",
    )
    markdown_body = notification_routing_service.render_custom_template(
        notification_routing_service.MARKDOWN_BACKUP_BODY_TEMPLATE,
        context,
        content_type="markdown",
    )

    assert "max-width:1200px" in html_body
    assert "vertical-align:middle" in html_body
    assert "#198754" in html_body
    assert "#dc3545" in html_body
    labels = context["labels"]
    assert labels["failed_section"] in markdown_body
    assert labels["changed_section"] in markdown_body
    assert labels["error"] not in markdown_body
    assert labels["change_summary"] not in markdown_body
    assert f'| {labels["device_name"]} | {labels["device_host"]} | {labels["duration"]} | {labels["failure_type"]} |' in markdown_body
    assert f'### {labels["changed_section"]}\n| {labels["device_name"]} | {labels["device_host"]} |' in markdown_body
    failed_item = next(item for item in context["items"] if not item["success"] and not item["cancelled"])
    changed_item = next(item for item in context["items"] if item["changed"])
    for item in context["items"]:
        assert f'`{item["device_host"]}`' in markdown_body
        escaped_host = item["device_host"].replace(".", "\\.")
        assert f"`{escaped_host}`" not in markdown_body
    assert failed_item["error_message"] not in markdown_body
    assert changed_item["change_context_label"] not in markdown_body
    assert "`+ logging host 192.0.2.200`" not in markdown_body


@pytest.mark.parametrize(
    ("channel_type", "expected_message_type"),
    [("wecom", "markdown"), ("dingtalk", "markdown"), ("feishu", "interactive")],
)
def test_robot_channels_can_reuse_saved_secrets_for_test_messages(
    monkeypatch,
    channel_type,
    expected_message_type,
):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    posted: list[dict] = []
    monkeypatch.setattr(
        notification_routing_service,
        "_post_json",
        lambda url, data, **kwargs: posted.append({"url": url, "data": data, **kwargs}),
    )

    with Session(engine) as session:
        channel = notification_routing_service.save_channel(
            session,
            channel_id=None,
            name=f"Test {channel_type}",
            channel_type=channel_type,
            enabled=True,
            config={"timeout": 10, "allow_private": False},
            secrets={
                "url": f"https://{channel_type}.example.invalid/hook",
                "signing_secret": "saved-signing-secret",
            },
        )
        success = notification_routing_service.test_channel(
            session,
            channel_id=channel.id,
            channel_type=channel_type,
            config={"timeout": 10, "allow_private": False},
            secrets={"url": "", "signing_secret": "", "authorization": ""},
            subject="EasyNetBak test",
            content="Channel test message",
        )

    assert success is True
    assert len(posted) == 1
    assert posted[0]["data"]["msgtype" if channel_type != "feishu" else "msg_type"] == expected_message_type


def test_notification_channel_modal_exposes_robot_test_button():
    root = Path(__file__).resolve().parents[1]
    template_source = (root / "app" / "templates" / "notifications.html").read_text(encoding="utf-8-sig")
    script_source = (root / "app" / "static" / "js" / "pages" / "notifications.js").read_text(encoding="utf-8-sig")

    assert "notification.channel.test" in template_source
    assert '["wecom", "dingtalk", "feishu"]' in script_source
    assert "isSmtp || isRobot" in script_source
    assert 'item.password_mask || ""' in script_source
    assert 'item.url_mask || ""' in script_source
    assert 'item.signing_secret_mask || ""' in script_source
    assert 'item.authorization_mask || ""' in script_source
    assert "function setChannelType(value)" in script_source
    assert 'channelModal?.addEventListener("shown.bs.modal"' in script_source
    assert 'option.value === "webhook"' in script_source
    assert "channelType.selectedIndex = -1" in script_source
    assert "window.NB?.refreshSelectDropdowns?.()" in script_source
    assert "notifications-ui-8" in template_source


def test_notification_smtp_modal_documents_multiple_recipients_and_starttls():
    root = Path(__file__).resolve().parents[1]
    template_source = (root / "app" / "templates" / "notifications.html").read_text(encoding="utf-8-sig")

    assert 'name="smtp_to" multiple' in template_source
    assert "notification.smtp.recipients_hint" in template_source
    assert "notification.smtp.guidance.sender.body" in template_source
    assert "notification.smtp.guidance.authorization.body" in template_source
    assert "notification.smtp.security.body" in template_source


def test_notification_page_renders_feishu_cards_without_gradient_styling():
    root = Path(__file__).resolve().parents[1]
    script_source = (root / "app" / "static" / "js" / "pages" / "notifications.js").read_text(encoding="utf-8-sig")
    style_source = (root / "app" / "static" / "css" / "pages" / "notifications.css").read_text(encoding="utf-8-sig")

    assert "renderFeishuCardPreview" in script_source
    assert 'cardData?.schema !== "2.0"' in script_source
    assert "feishu-preview-table" in script_source
    assert "renderWebhookPayloadPreview" in script_source
    assert "webhook-preview-summary" in script_source
    assert "webhook-preview-table" in script_source
    assert "status-badge" in style_source
    assert "state-action" in style_source
    assert "notifications-ui-8" in (root / "app" / "templates" / "notifications.html").read_text(encoding="utf-8-sig")
    assert "linear-gradient" not in style_source


def test_feishu_builtin_template_uses_compact_native_tables_with_row_limits(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(crud, "get_setting", lambda session, key: None)
    posted: list[dict] = []
    monkeypatch.setattr(
        notification_routing_service,
        "_post_json",
        lambda url, data, **kwargs: posted.append({"url": url, "data": data, **kwargs}),
    )

    failed = [
        {
            "device_name": f"failed-{index}",
            "device_host": f"192.0.2.{index}",
            "duration": f"{index}s",
            "failure_type": "TIMEOUT",
            "error_message": "failure detail must not be displayed",
            "success": False,
            "cancelled": False,
            "changed": False,
        }
        for index in range(21)
    ]
    cancelled = [
        {
            "device_name": f"cancelled-{index}",
            "device_host": f"198.51.100.{index}",
            "duration": f"{index}s",
            "failure_type": "CANCELLED",
            "error_message": "cancellation detail must not be displayed",
            "success": False,
            "cancelled": True,
            "changed": False,
        }
        for index in range(21)
    ]
    changed = [
        {
            "device_name": f"changed-{index}",
            "device_host": f"203.0.113.{index}",
            "duration": f"{index}s",
            "success": True,
            "cancelled": False,
            "changed": True,
            "change_lines": [{"prefix": "+", "text": "change summary must not be displayed", "kind": "add"}],
        }
        for index in range(21)
    ]
    context = notification_routing_service.normalize_backup_payload(
        {"task_time": "2026-07-20 10:00:00", "items": [*failed, *cancelled, *changed]},
        "zh-CN",
    )
    context.update(notification_routing_service._feishu_table_context(context))
    body = notification_routing_service.render_custom_template(
        notification_routing_service.FEISHU_BACKUP_BODY_TEMPLATE,
        context,
        content_type="json",
    )

    with Session(engine) as session:
        notification_routing_service.ensure_builtin_defaults(session)
        templates_by_key = {
            template.builtin_key: template
            for template in session.exec(select(NotificationTemplate)).all()
            if template.builtin_key
        }
        assert templates_by_key[notification_routing_service.BUILTIN_FEISHU_TEMPLATE_KEY].channel_type == "feishu"
        assert templates_by_key[notification_routing_service.BUILTIN_FEISHU_TEMPLATE_KEY].content_type == "json"
        assert notification_routing_service._resolve_template_for_channel(
            session,
            templates_by_key[notification_routing_service.BUILTIN_DETAILED_TEMPLATE_KEY_V2],
            "feishu",
        ).builtin_key == notification_routing_service.BUILTIN_FEISHU_TEMPLATE_KEY

        channel = notification_routing_service.save_channel(
            session,
            channel_id=None,
            name="Feishu",
            channel_type="feishu",
            enabled=True,
            config={"timeout": 10, "allow_private": False},
            secrets={"url": "https://feishu.example.invalid/hook"},
        )
        notification_routing_service._send_channel(
            channel,
            subject="飞书备份汇总",
            body=body,
            content_type="json",
            payload=context,
        )

    card = posted[0]["data"]["card"]
    tables = [element for element in card["body"]["elements"] if element["tag"] == "table"]
    serialized = json.dumps(card, ensure_ascii=False)
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "飞书备份汇总"
    assert len(tables) == 3
    assert [len(table["rows"]) for table in tables] == [20, 20, 20]
    assert [table["page_size"] for table in tables] == [10, 10, 10]
    assert [[column["name"] for column in table["columns"]] for table in tables] == [
        ["device_name", "device_host", "duration", "failure_type"],
        ["device_name", "device_host"],
        ["device_name", "device_host"],
    ]
    assert serialized.count("另有 1 台未展示，详情登录系统查看") == 3
    assert "failure detail must not be displayed" not in serialized
    assert "cancellation detail must not be displayed" not in serialized
    assert "change summary must not be displayed" not in serialized
    assert len(json.dumps(posted[0]["data"], ensure_ascii=False).encode("utf-8")) < notification_routing_service.FEISHU_CARD_MAX_BYTES
def test_frontend_foundation_load_order_and_data_boundaries():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8-sig")

    assert source.index("/static/css/tokens.css") < source.index("/static/css/app.css")

    expected_order = (
        "/static/js/core/i18n.js",
        "/static/js/core/page.js",
        "/static/js/bootstrap.bundle.min.js",
        "/static/js/core/api.js",
        "/static/js/core/ui-feedback.js",
        "/static/js/core/app-shell.js",
        'id="nb-task-config"',
        "/static/js/core/nb-common.js",
    )
    positions = [source.index(marker) for marker in expected_order]
    assert positions == sorted(positions)
    assert 'type="application/json" id="nb-task-config"' in source
    assert 'type="application/json" id="nb-watermark-config"' in source
    assert "window.NB_TASK_CONFIG" not in source
    assert 'createWatermark("{{ current_user.username }}")' not in source


def test_static_scripts_use_the_shared_page_lifecycle():
    root = Path(__file__).resolve().parents[1]
    javascript_root = root / "app" / "static" / "js"
    offenders = []
    for path in javascript_root.rglob("*.js"):
        if path.name.endswith(".min.js") or path.name == "page.js":
            continue
        source = path.read_text(encoding="utf-8-sig")
        if "DOMContentLoaded" in source:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_frontend_foundation_files_are_loaded_and_nonempty():
    root = Path(__file__).resolve().parents[1]
    core = root / "app" / "static" / "js" / "core"
    for name in ("page.js", "app-shell.js", "watermark.js"):
        source = (core / name).read_text(encoding="utf-8-sig")
        assert len(source.splitlines()) >= 20

    task_panel = (core / "nb-common.js").read_text(encoding="utf-8-sig")
    assert 'NB.readJson("nb-task-config", {})' in task_panel
    assert "initTaskPanel" in task_panel

    config_search_template = (root / "app" / "templates" / "config_search.html").read_text(encoding="utf-8-sig")
    assert 'type="application/json" id="config-search-config"' in config_search_template
    assert "/static/js/pages/config-search.js" in config_search_template
    assert "DOMContentLoaded" not in config_search_template

    config_search_script = (root / "app" / "static" / "js" / "pages" / "config-search.js").read_text(encoding="utf-8-sig")
    assert "{{" not in config_search_script
    assert 'NB.readJson("config-search-config", {})' in config_search_script
    assert "escapeText(configText).replace(regex" in config_search_script
    assert "fetch(" not in config_search_script
    config_search_messages = javascript_messages("en-US", page="config_search")
    assert "email.field.backup_time" in config_search_messages
    assert "template.config_search.view_configuration" in config_search_messages
    assert "template.config_search.no_configuration_content" in config_search_messages


def test_low_risk_frontend_governance_contracts():
    root = Path(__file__).resolve().parents[1]
    templates_root = root / "app" / "templates"
    javascript_root = root / "app" / "static" / "js"

    inline_handler = re.compile(r"\s(?:onclick|onchange|onsubmit|oninput|onkeydown|onkeyup)\s*=", re.IGNORECASE)
    offenders = []
    for path in templates_root.rglob("*.html"):
        if inline_handler.search(path.read_text(encoding="utf-8-sig")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []

    direct_fetch_allowlist = {
        "app/templates/backups.html",
        "app/templates/device_detail.html",
    }
    direct_fetch_offenders = set()
    for base in (templates_root, javascript_root):
        for path in base.rglob("*"):
            if path.suffix not in {".html", ".js"} or path.name.endswith(".min.js") or path.name == "api.js":
                continue
            if re.search(r"\bfetch\s*\(", path.read_text(encoding="utf-8-sig")):
                direct_fetch_offenders.add(str(path.relative_to(root)).replace("\\", "/"))
    assert direct_fetch_offenders == direct_fetch_allowlist

    migrated_pages = {
        "login.html": "auth-login.js",
        "credentials.html": "credentials.js",
        "api_keys.html": "api-keys.js",
        "templates.html": "templates.js",
        "storage_settings.html": "storage-settings.js",
        "config_search.html": "config-search.js",
    }
    for template_name, script_name in migrated_pages.items():
        template_source = (templates_root / template_name).read_text(encoding="utf-8-sig")
        assert f"/static/js/pages/{script_name}" in template_source
        script_source = (javascript_root / "pages" / script_name).read_text(encoding="utf-8-sig")
        assert "{{" not in script_source
        assert "NB.ready(function init" in script_source

    tokens = (root / "app" / "static" / "css" / "tokens.css").read_text(encoding="utf-8-sig")
    assert ":root" in tokens
    assert '[data-bs-theme="dark"]' in tokens


def test_css_duplicate_cleanup_preserves_structure_and_scopes():
    import hashlib

    root = Path(__file__).resolve().parents[1]
    app_css = (root / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8-sig")
    notifications_css = (
        root / "app" / "static" / "css" / "pages" / "notifications.css"
    ).read_text(encoding="utf-8-sig")

    def assert_balanced_css(source):
        depth = 0
        index = 0
        quote = None
        in_comment = False
        while index < len(source):
            current = source[index]
            following = source[index + 1] if index + 1 < len(source) else ""
            if in_comment:
                if current == "*" and following == "/":
                    in_comment = False
                    index += 2
                    continue
            elif quote:
                if current == "\\":
                    index += 2
                    continue
                if current == quote:
                    quote = None
            elif current == "/" and following == "*":
                in_comment = True
                index += 2
                continue
            elif current in {'"', "'"}:
                quote = current
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                assert depth >= 0
            index += 1
        assert not in_comment
        assert quote is None
        assert depth == 0

    assert_balanced_css(app_css)
    assert_balanced_css(notifications_css)

    retained_blocks = {
        "/* Layout */": (91, "c0e0e3fcc1c6c932cff39a3f296de01fb208e53d4d865a39599d083749de1a1f"),
        "/* Toast Custom Styles */": (56, "3c483bd88687fad8c53f88fc01374246ce34eec56a7f5630213675f63ca85e0d"),
        "/* Content Header & Breadcrumbs */": (82, "80ed5b1c139fb9e93cc7adb4b4c1a4417a85a3c9d7558417e9164ec7319e9997"),
        "/* Cards */": (18, "ec0f07be6feed98cf62235609d41291fe8f57116eddeed0115b820b7a17be16c"),
        "/* Forms & Inputs */": (115, "b08f2240d9d27ad0af455ebb490c9c6675ec34e285647ee47efd8326e81a2e30"),
    }
    app_lines = app_css.splitlines()
    for marker, (line_count, expected_hash) in retained_blocks.items():
        assert app_lines.count(marker) == 1
        start = app_lines.index(marker)
        block = "\n".join(app_lines[start : start + line_count])
        assert hashlib.sha256(block.encode()).hexdigest() == expected_hash

    assert app_css.count("--premium-shadow:") == 1
    assert app_css.count("--premium-shadow-lg:") == 1
    assert app_css.count("--sidebar-width: 280px;") == 1

    assert ".notification-console,\n#templateModal {" in notifications_css
    assert (
        '[data-bs-theme="dark"] .notification-console,\n'
        '[data-bs-theme="dark"] #templateModal {'
    ) in notifications_css
    for declaration in (
        "--signal-ink: #172033;",
        "--signal-muted: #657189;",
        "--signal-blue: #2457d6;",
        "--signal-line: #dfe5ef;",
        "--signal-paper: #f7f9fc;",
        "--signal-ink: #edf2ff;",
        "--signal-muted: #a9b4c9;",
        "--signal-line: #35405a;",
        "--signal-paper: #20283a;",
    ):
        assert notifications_css.count(declaration) == 1
