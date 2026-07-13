from __future__ import annotations

import json

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


def test_api_error_legacy_fallback_message_is_localized():
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
    assert translate_legacy_text("Search users名orIPaddress", "en-US") == "Search username or IP address"
    assert translate_legacy_text("UseDeviceDefaultTemplate", "en-US") == "Use device default template"
    assert translate_legacy_text("SelectDevice", "en-US") == "Select device"
    assert translate_legacy_text("Configuration change frequency (最近7天)", "en-US") == (
        "Configuration change frequency (Last 7 days)"
    )
    assert translate_legacy_text("为什么要ConfigurationIgnore rules?", "en-US") == "Why configure ignore rules?"
    assert translate_legacy_text("规则Configuration notes:", "en-US") == "Rule configuration notes:"
    assert translate_legacy_text("通过AddIgnore rules，只关注真正的Configuration更改。", "en-US") == (
        "By adding ignore rules, and focus only on real configuration changes."
    )
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
    assert translate_legacy_text("已禁用", "en-US") == "Disabled"
    assert translate_legacy_text("未配置", "en-US") == "Not configured"
    assert translate_legacy_text("Cron 无效", "en-US") == "Invalid cron expression"
    assert translate_legacy_text("无后续执行", "en-US") == "No future runs"
    assert translate_legacy_text("编辑分组", "en-US") == "Edit group"
    assert translate_legacy_text("搜索...", "en-US") == "Search..."
    assert translate_legacy_text("未分组", "en-US") == "Ungrouped"
    assert translate_legacy_text("正在连接...", "en-US") == "Connecting..."
    assert translate_legacy_text("连接成功 (耗时: 1.2s)", "en-US") == "Connected (Duration: 1.2s)"
    assert translate_legacy_text(
        "Celery 未配置 broker，异步任务入队能力不可用。",
        "en-US",
    ) == "The Celery broker is not configured, so asynchronous tasks cannot be queued."


def test_screenshot_reported_mixed_ui_text_is_localized():
    assert translate_legacy_text("所属分组: 全部", "en-US") == "Group: All"
    assert translate_legacy_text("为什么要ConfigurationIgnore rules?", "en-US") == "Why configure ignore rules?"
    assert translate_legacy_text(
        "通过AddIgnore rules, you can filter out this noise, 只关注真正的Configuration更改。",
        "en-US",
    ) == "By adding ignore rules, you can filter out this noise and focus only on real configuration changes."
    assert translate_legacy_text(
        "Device group: 仅应用于特定Group内的Device。",
        "en-US",
    ) == "Device group: Applies only to devices in the selected group."
    assert translate_legacy_text(
        "Cron Expression (APScheduler: 分 时 day 月 周)",
        "en-US",
    ) == "Cron expression (APScheduler: minute hour day month weekday)"
    assert translate_legacy_text(
        "控制同时进Row的Backup tasks及批量检测Tasks的数量.建议值: 10-30",
        "en-US",
    ) == "Control concurrent backup and bulk test task counts. Recommended: 10-30."
    assert translate_legacy_text(
        "SMTP ServerConfiguration",
        "en-US",
    ) == "SMTP server configuration"
    assert translate_legacy_text(
        "超过此days数的Audit logs将被自动清理, Default 180.",
        "en-US",
    ) == "Audit logs older than this many days are cleaned automatically. Default: 180."
    assert translate_legacy_text(
        "超过此days数的Login logs将被自动清理, Default 180.",
        "en-US",
    ) == "Login logs older than this many days are cleaned automatically. Default: 180."
    assert translate_legacy_text(
        "超过此days数的 Webshell Actions录像将被自动清理, Default 30.",
        "en-US",
    ) == "WebShell recordings older than this many days are cleaned automatically. Default: 30."
    assert translate_legacy_text(
        "When enabled, Configuration信息除Saveat数据库还会自动上传副本到指定的 S3 兼容Storage桶",
        "en-US",
    ) == "When enabled, configuration is saved in the database and also uploaded to the specified S3-compatible bucket."
    assert translate_legacy_text(
        "When enabled, Configuration信息除Saveat数据库还会自动上传副本到 FTP Server",
        "en-US",
    ) == "When enabled, configuration is saved in the database and also uploaded to the FTP server."
    assert translate_legacy_text(
        "给这items API Key 起一items易于识别的名字",
        "en-US",
    ) == "Give this API key an easy-to-recognize name."
    assert translate_legacy_text("Password长度建议不少于 8 位", "en-US") == (
        "Password should be at least 8 characters"
    )
    assert translate_legacy_text(
        "勾选Group以分配Permissions;Granting a parent automatically includes all descendants.",
        "en-US",
    ) == "Select groups to assign permissions. Granting a parent automatically includes all descendants."
    assert translate_legacy_text("所有Group (无限制)", "en-US") == "All groups (unrestricted)"
    assert translate_legacy_text("对于 QQ、163 等公Total邮箱, 通常需要Use", "en-US") == (
        "For public mail providers such as QQ and 163, you usually need "
    )
    assert translate_legacy_text(
        "Recovery code作为AdministratorEnableMFA后一组一次性Use的备用Verification code, "
        "old recovery codes become invalid immediately after regeneration, "
        "each recovery code can only be used once, used codes become invalid",
        "en-US",
    ) == (
        "Recovery codes are one-time backup verification codes generated after an administrator enables MFA. "
        "Old recovery codes become invalid immediately after regeneration, and each code can only be used once."
    )
    assert translate_legacy_text("System员 (Full Access)", "en-US") == "System admin"
    assert translate_legacy_text("Actions员", "en-US") == "Operator"
    assert translate_legacy_text("Read-only用户", "en-US") == "Read-only user"
    assert translate_legacy_text("Confirm要Delete此Group??", "en-US") == "Delete this group?"
    assert translate_legacy_text("Confirm要Delete此Template??", "en-US") == "Delete this template?"
    assert translate_legacy_text("Confirm要Delete此用户??", "en-US") == "Delete this user?"
    assert translate_legacy_text("Confirm要Delete此Role??", "en-US") == "Delete this role?"
    assert translate_legacy_text("Confirm要Revoke API Key", "en-US") == "Revoke API key"
    assert translate_legacy_text("Confirm要Delete permanently API Key", "en-US") == "Delete API key permanently"
    assert translate_legacy_text(
        "This action cannot be undone! If you only want to disable it temporarily, useRevokeFeature.",
        "en-US",
    ) == "This action cannot be undone. If you only want to disable it temporarily, use the revoke feature."
    assert translate_legacy_text(
        "拥有最高ManagePermissions, 可ManageAll devices、用户及完整审计Logs",
        "en-US",
    ) == "Has full administrative access to manage devices, users, and audit logs."
    assert translate_legacy_text("避免与Username过于相似", "en-US") == "Avoid making it too similar to the username."
    assert translate_legacy_text("组合大/小写字母、数字和符day", "en-US") == (
        "Use a mix of uppercase and lowercase letters, numbers, and symbols."
    )
    assert translate_legacy_text("建议每 90 days更换一次Password", "en-US") == "Change your password every 90 days."
    assert translate_legacy_text("S3 连接成功，写入权限验证通过。", "en-US") == (
        "S3 connected. Write permission verified."
    )
    assert translate_legacy_text("✅ S3 Connected, 写入PermissionsVerify通过.", "en-US") == (
        "✅ S3 connected. Write permission verified."
    )
    assert translate_legacy_text(
        "❌ FTP 连接Failed（Path encoding: utf-8）: [WinError 10061] 由于目标计算机积极拒绝，无法连接.",
        "en-US",
    ) == (
        "❌ FTP connection failed (path encoding: utf-8): "
        "[WinError 10061] The target computer actively refused the connection."
    )
    assert translate_legacy_text("Device已Delete", "en-US") == "Device deleted"
    assert translate_legacy_text("已Save", "en-US") == "Saved"
    assert translate_legacy_text("已Delete", "en-US") == "Deleted"
    assert translate_legacy_text("Confirm要Delete此Group??", "en-US") == "Delete this group?"
    assert translate_legacy_text("Confirm要Delete此Template??", "en-US") == "Delete this template?"
    assert translate_legacy_text("Confirm要Delete此用户??", "en-US") == "Delete this user?"
    assert translate_legacy_text("Confirm要Delete此Role??", "en-US") == "Delete this role?"
    assert translate_legacy_text("Confirm要Delete此Schedules??", "en-US") == "Delete this schedule?"
    assert translate_legacy_text("Confirm要Permanent移除这itemsIgnore rules??", "en-US") == (
        "Permanently remove these ignore rules?"
    )
    assert translate_legacy_text("确定要删除此分组吗？", "en-US") == "Delete this group?"
    assert translate_legacy_text("确定要删除此模板吗？", "en-US") == "Delete this template?"
    assert translate_legacy_text("确定要删除此用户吗？", "en-US") == "Delete this user?"
    assert translate_legacy_text("确定要删除此角色吗？", "en-US") == "Delete this role?"
    assert translate_legacy_text("确定要删除此定时任务吗？", "en-US") == "Delete this schedule?"
    assert translate_legacy_text("确定要永久移除这条忽略规则吗？", "en-US") == "Permanently remove this ignore rule?"


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


def test_email_legacy_content_is_localized():
    value = _localize_email_text("【告警】设备备份失败: core-1<br>错误详情: 未知错误", "en-US")
    assert "Device backup failed" in value
    assert "Error details" in value
    assert "Unknown error" in value
