from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.i18n import get_current_locale, reset_current_locale, set_current_locale, translate
from app.i18n.middleware import i18n_http_middleware, resolve_request_locale
from app.i18n.openapi import build_openapi_schema
from app.i18n.legacy import translate_legacy_text
from app.i18n.validators import normalize_locale, validate_locale
from app.main import _api_error_json, app as main_app
from app.services.audit_service import translate_audit_action, translate_audit_resource, translate_login_fail_reason
from app.models import TaskEvent
from app.routers.web_context import templates
from app.services import task_realtime_service, task_state_service
from app.services.alert_service import _localize_email_text


def test_catalog_translation_params_and_fallback():
    assert translate("en-US", "button.save") == "Save"
    assert translate("zh-CN", "button.save") == "保存"
    assert translate("en-US", "missing", {"name": "Alice"}, "Hello {name}") == "Hello Alice"
    assert translate("en-US", "missing") == "missing"


def test_locale_normalization_and_validation():
    assert normalize_locale("zh") == "zh-CN"
    assert normalize_locale("en") == "en-US"
    assert validate_locale("en-us") == "en-US"
    try:
        validate_locale("fr-FR")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported locale must be rejected")


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

    assert client.get("/user?lang=en-US").json() == {"locale": "zh-CN", "persist": False}


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
    assert response.cookies["nb_locale"] == "en-US"


def test_api_error_keeps_code_and_localizes_message():
    token = set_current_locale("en-US")
    try:
        response = _api_error_json(status_code=401, code="UNAUTHORIZED", message="Unauthorized")
    finally:
        reset_current_locale(token)
    assert b'"code":"UNAUTHORIZED"' in response.body
    assert b'"message":"Unauthorized or session expired"' in response.body


def test_openapi_and_audit_translation():
    schema = build_openapi_schema(main_app, "en-US")
    assert schema["info"]["title"] == "EasyNetBak API"
    assert "API documentation" in schema["info"]["description"]
    assert translate_audit_action("CREATE_DEVICE", "en-US") == "Create device"
    assert translate_audit_resource("device", "en-US") == "Device"
    assert translate_login_fail_reason("invalid_mfa", "en-US") == "Invalid verification code"


def test_legacy_template_literals_and_statuses_are_localized():
    template = templates.env.from_string("<button>取消</button><h1>设备管理</h1>")
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


def test_native_legacy_literals_and_mixed_ui_text_are_localized():
    assert translate_legacy_text("最近7天BACKUP TASKS", "en-US") == "Last 7 days backup tasks"
    assert translate_legacy_text("请在左侧SelectDevice以View其Backup history", "en-US") == (
        "Select a device on the left to view backup history"
    )
    assert translate_legacy_text("为什么要ConfigurationIgnore rules?", "en-US") == "Why configure ignore rules?"


def test_recent_mixed_legacy_ui_text_is_localized():
    assert translate_legacy_text("成功", "en-US") == "Succeeded"
    assert translate_legacy_text("失败", "en-US") == "Failed"
    assert translate_legacy_text("SearchDevice name或IP...", "en-US") == "Search device name or IP..."
    assert translate_legacy_text("为什么要ConfigurationIgnore rules?", "en-US") == "Why configure ignore rules?"
    assert translate_legacy_text("规则Configuration notes:", "en-US") == "Rule configuration notes:"
    assert translate_legacy_text(
        "示例：Cisco 使用 show running-config；华为/H3C 使用 display current-configuration",
        "en-US",
    ) == "Example: Cisco uses show running-config; Huawei/H3C uses display current-configuration"
    assert translate_legacy_text(
        "Cron 表达式 (APScheduler: 分 时 日 月 周)",
        "en-US",
    ) == "Cron expression (APScheduler: minute hour day month weekday)"
    assert translate_legacy_text("未选择任何文件", "en-US") == "No file selected"
    assert translate_legacy_text("请填写此字段。", "en-US") == "Please fill out this field."


def test_task_events_return_localized_message_and_stable_payload():
    event = TaskEvent(event="backup_record_command_started", details='{"command":"show running-config"}')
    item = task_realtime_service._serialize_task_event(event, offset_minutes=0, locale="en-US")
    assert item["message"] == "Running command: show running-config"
    assert item["message_key"] == "task.event.backup_record_command_started"
    assert item["message_params"] == {"command": "show running-config"}


def test_email_legacy_content_is_localized():
    value = _localize_email_text("【告警】设备备份失败: core-1<br>错误详情: 未知错误", "en-US")
    assert "Device backup failed" in value
    assert "Error details" in value
    assert "Unknown error" in value
