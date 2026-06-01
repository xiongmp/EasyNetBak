from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import crud
from app.models import BackupSchedule, Credential, Device, User
from app.schemas.inputs import (
    AuditLogListQueryInput,
    BaseListQueryInput,
    ConfigSearchListQueryInput,
    DeviceCreateInput,
    DeviceListQueryInput,
    EditableListQueryInput,
)
import app.celery_tasks as celery_tasks
from app.services import (
    alert_service,
    api_key_management_service,
    backup_service,
    device_service,
    identity_service,
    schedule_service,
    task_dispatcher_service,
    task_execution_service,
    task_orchestration_service,
    task_runtime_config_service,
    task_state_service,
)


@pytest.fixture()
def sqlite_session_factory(tmp_path):
    db_path = tmp_path / "test_identity_queue.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def factory():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return factory


def test_upsert_role_keeps_admin_code_immutable(sqlite_session_factory):
    with sqlite_session_factory() as session:
        identity_service.ensure_default_roles(session)
        admin_role = next(role for role in crud.list_roles(session) if role.code == "admin")

        with pytest.raises(identity_service.ServiceError):
            identity_service.upsert_role(
                session,
                role_id=int(admin_role.id),
                code="changed-code",
                name="系统管理员",
                permission_codes=["devices.view"],
            )


def test_load_backup_task_runtime_config_reads_db_settings(sqlite_session_factory, monkeypatch):
    with sqlite_session_factory() as session:
        crud.set_setting(session, key="max_concurrent_tasks", value="7")
        crud.set_setting(session, key="backup_max_retries", value="4")
        crud.set_setting(session, key="backup_retry_backoff", value="12")
        crud.set_setting(session, key="task_time_limit", value="180")

    monkeypatch.setattr(task_runtime_config_service, "session_scope", sqlite_session_factory)

    config = task_runtime_config_service.load_backup_task_runtime_config()

    assert config.max_slots == 7
    assert config.max_retries == 4
    assert config.backoff_base == 12
    assert config.time_limit == 180


def test_dispatch_backup_record_task_sets_time_limits():
    task = Mock()

    task_dispatcher_service.dispatch_backup_record_task(
        task,
        record_id="abc-123",
        device_id=5,
        template_id=None,
        skip_email=True,
        time_limit=120,
    )

    task.apply_async.assert_called_once_with(
        args=["abc-123", 5, None],
        kwargs={"skip_email": True},
        task_id="abc-123",
        time_limit=120,
        soft_time_limit=110,
    )


def test_task_state_service_exposes_backup_record_status_metadata():
    assert task_state_service.get_backup_record_status_label("queued") == "已入队"
    assert task_state_service.get_backup_record_status_tone("queued") == "info"
    assert task_state_service.is_backup_record_active_status("queued") is True
    assert task_state_service.get_backup_record_status_label("cancelled") == "已终止"
    assert task_state_service.is_backup_record_terminal_status("cancelled") is True


def test_task_state_service_exposes_schedule_run_status_metadata():
    assert task_state_service.get_schedule_run_status_label("finalizing") == "收尾中"
    assert task_state_service.get_schedule_run_status_tone("partial_failed") == "failed"
    assert task_state_service.is_schedule_run_active_status("finalizing") is True
    assert task_state_service.get_schedule_run_status_label("cancelling") == "终止中"
    assert task_state_service.get_schedule_run_status_tone("partial_cancelled") == "warning"


def test_enqueue_single_backup_marks_record_failed_when_celery_disabled(sqlite_session_factory, monkeypatch):
    monkeypatch.setattr(celery_tasks, "celery_enabled", lambda: False)

    with sqlite_session_factory() as session:
        device = Device(name="edge-single", host="10.0.10.1", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        planned = task_orchestration_service.plan_single_backup(session, device_id=int(device.id))
        enqueued = task_orchestration_service.enqueue_single_backup(
            session,
            planned=planned,
            skip_email=True,
        )

        record = crud.get_backup(session, planned.record_id)
        assert enqueued is False
        assert record is not None
        assert record.status == task_state_service.BACKUP_RECORD_STATUS_FAILED
        assert record.finished_at is not None
        assert record.failure_type == "ENQUEUE_FAILED"


def test_enqueue_schedule_run_dispatches_finalizer_before_backup_jobs(sqlite_session_factory, monkeypatch):
    monkeypatch.setattr(celery_tasks, "celery_enabled", lambda: True)
    monkeypatch.setattr(task_runtime_config_service, "load_task_time_limit", lambda: 180)

    calls: list[str] = []

    def fake_dispatch_schedule_finalizer(task_func, *, run_id, backup_ids, countdown):
        calls.append("finalizer")

    def fake_dispatch_backup_record_task(task_func, *, record_id, device_id, template_id, skip_email, time_limit):
        calls.append(f"backup:{device_id}")

    monkeypatch.setattr(task_dispatcher_service, "dispatch_schedule_finalizer", fake_dispatch_schedule_finalizer)
    monkeypatch.setattr(task_dispatcher_service, "dispatch_backup_record_task", fake_dispatch_backup_record_task)

    with sqlite_session_factory() as session:
        session.add(Device(name="edge-b1", host="10.0.20.1", platform="cisco_ios"))
        session.add(Device(name="edge-b2", host="10.0.20.2", platform="huawei"))
        session.commit()
        device_ids = [int(device.id) for device in crud.list_devices(session) if device.id]

        run_id, jobs = task_orchestration_service.plan_device_batch_run(
            session,
            device_ids=device_ids,
            trigger="manual",
            schedule_id=0,
        )
        enqueued = task_orchestration_service.enqueue_schedule_run(
            session,
            run_id=run_id,
            jobs=jobs,
            skip_email=True,
        )

        assert enqueued is True
        assert calls[0] == "finalizer"
        assert calls[1:] == [f"backup:{device_id}" for device_id in device_ids]
        run = crud.get_schedule_run(session, run_id)
        assert run is not None
        assert run.status == task_state_service.SCHEDULE_RUN_STATUS_RUNNING


def test_finalize_schedule_run_requests_retry_when_backups_pending(sqlite_session_factory):
    with sqlite_session_factory() as session:
        device = Device(name="edge-pending", host="10.0.30.1", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=1)
        record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)

        decision = task_orchestration_service.finalize_schedule_run(
            session,
            run_id=run.id,
            backup_ids=[record.id],
            retries_done=0,
        )

        assert decision.should_retry is True
        assert decision.retry_countdown == 5
        assert decision.response["reason"] == "pending"
        updated_run = crud.get_schedule_run(session, run.id)
        assert updated_run is not None
        assert updated_run.status == task_state_service.SCHEDULE_RUN_STATUS_FINALIZING


def test_finalize_schedule_run_summarizes_finished_records(sqlite_session_factory):
    with sqlite_session_factory() as session:
        device_ok = Device(name="edge-ok", host="10.0.40.1", platform="cisco_ios")
        device_fail = Device(name="edge-fail", host="10.0.40.2", platform="huawei")
        session.add(device_ok)
        session.add(device_fail)
        session.commit()
        session.refresh(device_ok)
        session.refresh(device_fail)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=2)
        ok_record = crud.create_backup_record(session, device_id=int(device_ok.id), template_id=None)
        fail_record = crud.create_backup_record(session, device_id=int(device_fail.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=ok_record.id,
            success=True,
            config_text="ok",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )
        crud.finish_backup_record(
            session,
            record_id=fail_record.id,
            success=False,
            config_text=None,
            error_message="timeout",
            duration_seconds=2.0,
            failure_type="TIMEOUT",
        )

        decision = task_orchestration_service.finalize_schedule_run(
            session,
            run_id=run.id,
            backup_ids=[ok_record.id, fail_record.id],
            retries_done=999,
        )
        updated_run = crud.get_schedule_run(session, run.id)

        assert decision.should_retry is False
        assert decision.success_count == 1
        assert decision.fail_count == 1
        assert decision.response["ok"] is True
        assert updated_run is not None
        assert updated_run.status == task_state_service.SCHEDULE_RUN_STATUS_PARTIAL_FAILED
        assert updated_run.finished_at is not None
        assert updated_run.success_count == 1
        assert updated_run.fail_count == 1
        assert "TIMEOUT" in (updated_run.error_message or "")


def test_terminate_schedule_run_only_cancels_pending_records(sqlite_session_factory, monkeypatch):
    revoked: list[str] = []

    monkeypatch.setattr(
        task_orchestration_service,
        "_revoke_backup_task",
        lambda record_id: revoked.append(str(record_id)),
    )

    with sqlite_session_factory() as session:
        device_pending = Device(name="edge-term-pending", host="10.0.41.1", platform="cisco_ios")
        device_running = Device(name="edge-term-running", host="10.0.41.2", platform="huawei")
        session.add(device_pending)
        session.add(device_running)
        session.commit()
        session.refresh(device_pending)
        session.refresh(device_running)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=2)
        pending_record = crud.create_backup_record(session, device_id=int(device_pending.id), template_id=None)
        running_record = crud.create_backup_record(session, device_id=int(device_running.id), template_id=None)
        crud.update_backup_record_status(
            session,
            record_id=running_record.id,
            status=task_state_service.BACKUP_RECORD_STATUS_RUNNING,
        )
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=pending_record.id,
            device_id=int(device_pending.id),
        )
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=running_record.id,
            device_id=int(device_running.id),
        )

        result = task_orchestration_service.terminate_schedule_run(session, run_id=run.id)
        updated_run = crud.get_schedule_run(session, run.id)
        updated_pending = crud.get_backup(session, pending_record.id)
        updated_running = crud.get_backup(session, running_record.id)

        assert result.status == task_state_service.SCHEDULE_RUN_STATUS_CANCELLING
        assert result.terminated_records == 1
        assert result.running_records == 1
        assert revoked == [str(pending_record.id)]
        assert updated_run is not None
        assert updated_run.status == task_state_service.SCHEDULE_RUN_STATUS_CANCELLING
        assert updated_pending is not None
        assert updated_pending.status == task_state_service.BACKUP_RECORD_STATUS_CANCELLED
        assert updated_pending.finished_at is not None
        assert updated_running is not None
        assert updated_running.status == task_state_service.BACKUP_RECORD_STATUS_RUNNING


def test_terminate_schedule_run_finishes_cancelled_when_all_pending(sqlite_session_factory, monkeypatch):
    monkeypatch.setattr(task_orchestration_service, "_revoke_backup_task", lambda record_id: None)

    with sqlite_session_factory() as session:
        device = Device(name="edge-term-all", host="10.0.42.1", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=1)
        record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=record.id,
            device_id=int(device.id),
        )

        result = task_orchestration_service.terminate_schedule_run(session, run_id=run.id)
        updated_run = crud.get_schedule_run(session, run.id)
        updated_record = crud.get_backup(session, record.id)

        assert result.status == task_state_service.SCHEDULE_RUN_STATUS_CANCELLED
        assert updated_run is not None
        assert updated_run.status == task_state_service.SCHEDULE_RUN_STATUS_CANCELLED
        assert updated_run.finished_at is not None
        assert updated_record is not None
        assert updated_record.status == task_state_service.BACKUP_RECORD_STATUS_CANCELLED
        assert "cancelled_backups" in (updated_run.error_message or "")


def test_finalize_schedule_run_marks_partial_cancelled(sqlite_session_factory):
    with sqlite_session_factory() as session:
        device_ok = Device(name="edge-partial-cancel-ok", host="10.0.43.1", platform="cisco_ios")
        device_cancel = Device(name="edge-partial-cancel-stop", host="10.0.43.2", platform="huawei")
        session.add(device_ok)
        session.add(device_cancel)
        session.commit()
        session.refresh(device_ok)
        session.refresh(device_cancel)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=2)
        crud.update_schedule_run_status(
            session,
            run_id=run.id,
            status=task_state_service.SCHEDULE_RUN_STATUS_CANCELLING,
        )
        ok_record = crud.create_backup_record(session, device_id=int(device_ok.id), template_id=None)
        cancelled_record = crud.create_backup_record(session, device_id=int(device_cancel.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=ok_record.id,
            success=True,
            config_text="ok",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )
        crud.cancel_backup_record(
            session,
            record_id=cancelled_record.id,
            error_message="manual stop",
            failure_type="CANCELLED",
        )

        decision = task_orchestration_service.finalize_schedule_run(
            session,
            run_id=run.id,
            backup_ids=[ok_record.id, cancelled_record.id],
            retries_done=999,
        )
        updated_run = crud.get_schedule_run(session, run.id)

        assert decision.should_retry is False
        assert updated_run is not None
        assert updated_run.status == task_state_service.SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED
        assert updated_run.finished_at is not None
        assert "cancelled_backups" in (updated_run.error_message or "")


def test_check_and_alert_batch_sends_summary_for_cancelled_records(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_fail", value="1")
        crud.set_setting(session, key="alert_on_config_change", value="0")
        crud.set_setting(session, key="always_send_summary", value="0")

        device_ok = Device(name="edge-alert-ok", host="10.0.44.1", platform="cisco_ios")
        device_cancel = Device(name="edge-alert-cancel", host="10.0.44.2", platform="huawei")
        session.add(device_ok)
        session.add(device_cancel)
        session.commit()
        session.refresh(device_ok)
        session.refresh(device_cancel)

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=2)
        crud.finish_schedule_run(
            session,
            run_id=run.id,
            success_count=1,
            fail_count=0,
            cancelled_count=1,
            error_message='{"termination_mode":"pending_only","cancelled_backups":1}',
            status=task_state_service.SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED,
            unfinished_count=0,
        )
        ok_record = crud.create_backup_record(session, device_id=int(device_ok.id), template_id=None)
        cancelled_record = crud.create_backup_record(session, device_id=int(device_cancel.id), template_id=None)
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=ok_record.id,
            device_id=int(device_ok.id),
        )
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=cancelled_record.id,
            device_id=int(device_cancel.id),
        )
        crud.finish_backup_record(
            session,
            record_id=ok_record.id,
            success=True,
            config_text="ok",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )
        crud.cancel_backup_record(
            session,
            record_id=cancelled_record.id,
            error_message="未运行任务已被人工终止",
            failure_type="CANCELLED",
        )

        alert_service.check_and_alert_batch(session, run.id)

        assert len(sent) == 1
        subject, content, content_type = sent[0]
        assert "被终止" in subject
        assert "终止列表" in content
        assert "终止 <span" in content
        assert ">1</span> 台" in content
        assert content_type == "html"


def test_check_and_alert_skips_email_when_only_ignored_diff_rules_change(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_config_change", value="1")
        backup_service.save_diff_rules(
            session,
            [{"scope": "global", "targets": [], "patterns": [r"^ntp clock-period.*"]}],
        )

        device = Device(name="edge-noise-only", host="10.0.45.1", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        prev_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=prev_record.id,
            success=True,
            config_text="hostname edge-noise-only\nntp clock-period 12345\n",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        current_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=current_record.id,
            success=True,
            config_text="hostname edge-noise-only\nntp clock-period 67890\n",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        alert_service.check_and_alert(session, current_record)

        assert sent == []


def test_check_and_alert_includes_meaningful_diff_summary_in_email(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_config_change", value="1")
        backup_service.save_diff_rules(
            session,
            [{"scope": "global", "targets": [], "patterns": [r"^ntp clock-period.*"]}],
        )

        device = Device(name="edge-meaningful", host="10.0.45.3", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        prev_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=prev_record.id,
            success=True,
            config_text=(
                "hostname edge-meaningful\n"
                "interface GigabitEthernet0/1\n"
                " description to-aaaaaaaa\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                "ntp clock-period 12345\n"
                "line vty 0 4\n"
                " transport input ssh\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        current_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=current_record.id,
            success=True,
            config_text=(
                "hostname edge-meaningful\n"
                "interface GigabitEthernet0/1\n"
                " description to-bbb\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                "logging host 10.1.1.10\n"
                "ntp clock-period 67890\n"
                "line vty 0 4\n"
                " transport input ssh\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        alert_service.check_and_alert(session, current_record)

        assert len(sent) == 1
        subject, content, content_type = sent[0]
        assert "设备配置已变更" in subject
        assert "已应用 Diff 忽略规则" in content
        assert "变更片段（含前后各 2 行）" in content
        assert "interface GigabitEthernet0/1" in content
        assert "ip address 10.1.1.1 255.255.255.0" in content
        assert "+ logging host 10.1.1.10" in content
        assert "+  description to-bbb" in content
        assert "-  description to-aaaaaaaa" in content
        assert "ntp clock-period 67890" not in content
        assert content_type == "html"


def test_check_and_alert_includes_all_change_fragments_in_email(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_config_change", value="1")
        backup_service.save_diff_rules(
            session,
            [{"scope": "global", "targets": [], "patterns": [r"^ntp clock-period.*"]}],
        )

        device = Device(name="edge-all-fragments", host="10.0.45.30", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        prev_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=prev_record.id,
            success=True,
            config_text=(
                "hostname edge-all-fragments\n"
                "interface GigabitEthernet0/1\n"
                " description old-uplink\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                " no shutdown\n"
                "!\n"
                "line vty 0 4\n"
                " transport input ssh\n"
                "!\n"
                "router ospf 1\n"
                " network 10.1.1.0 0.0.0.255 area 0\n"
                " passive-interface default\n"
                " no passive-interface GigabitEthernet0/1\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        current_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=current_record.id,
            success=True,
            config_text=(
                "hostname edge-all-fragments\n"
                "interface GigabitEthernet0/1\n"
                " description new-uplink\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                " no shutdown\n"
                "!\n"
                "line vty 0 4\n"
                " transport input ssh\n"
                "!\n"
                "router ospf 1\n"
                " network 10.1.1.0 0.0.0.255 area 0\n"
                " network 10.2.2.0 0.0.0.255 area 0\n"
                " passive-interface default\n"
                " no passive-interface GigabitEthernet0/1\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        alert_service.check_and_alert(session, current_record)

        assert len(sent) == 1
        _, content, _ = sent[0]
        assert "description new-uplink" in content
        assert "description old-uplink" in content
        assert "network 10.2.2.0 0.0.0.255 area 0" in content
        assert "router ospf 1" in content
        assert "当前仅展示前" not in content


def test_check_and_alert_batch_includes_meaningful_diff_summary_in_email(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_fail", value="0")
        crud.set_setting(session, key="alert_on_config_change", value="1")
        crud.set_setting(session, key="always_send_summary", value="0")
        backup_service.save_diff_rules(
            session,
            [{"scope": "global", "targets": [], "patterns": [r"^ntp clock-period.*"]}],
        )

        device = Device(name="edge-batch-meaningful", host="10.0.45.4", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        prev_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=prev_record.id,
            success=True,
            config_text=(
                "hostname edge-batch-meaningful\n"
                "interface GigabitEthernet0/1\n"
                " description to-aaaaaaaa\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                "ntp clock-period 10000\n"
                "line vty 0 4\n"
                " transport input ssh\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=1)
        current_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=current_record.id,
            device_id=int(device.id),
        )
        crud.finish_schedule_run(
            session,
            run_id=run.id,
            success_count=1,
            fail_count=0,
            cancelled_count=0,
            error_message=None,
            status=task_state_service.SCHEDULE_RUN_STATUS_SUCCEEDED,
            unfinished_count=0,
        )
        crud.finish_backup_record(
            session,
            record_id=current_record.id,
            success=True,
            config_text=(
                "hostname edge-batch-meaningful\n"
                "interface GigabitEthernet0/1\n"
                " description to-bbb\n"
                " ip address 10.1.1.1 255.255.255.0\n"
                "logging host 10.1.1.20\n"
                "ntp clock-period 20000\n"
                "line vty 0 4\n"
                " transport input ssh\n"
            ),
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        alert_service.check_and_alert_batch(session, run.id)

        assert len(sent) == 1
        subject, content, content_type = sent[0]
        assert "发现 1 台设备配置变更" in subject
        assert "配置变更列表" in content
        assert "edge-batch-meaningful" in content
        assert "设备名称" in content
        assert "设备地址" in content
        assert "变更摘要" in content
        assert "变更片段（含前后各 2 行）" in content
        assert "interface GigabitEthernet0/1" in content
        assert "ip address 10.1.1.1 255.255.255.0" in content
        assert "+ logging host 10.1.1.20" in content
        assert "+  description to-bbb" in content
        assert "-  description to-aaaaaaaa" in content
        assert "#198754" in content
        assert "#dc3545" in content
        assert "#6c757d" in content
        assert "ntp clock-period 20000" not in content
        assert content_type == "html"


def test_check_and_alert_batch_skips_summary_when_only_ignored_diff_rules_change(sqlite_session_factory, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        alert_service,
        "send_email",
        lambda subject, content, content_type="html": sent.append((subject, content, content_type)),
    )

    with sqlite_session_factory() as session:
        crud.set_setting(session, key="alert_on_fail", value="0")
        crud.set_setting(session, key="alert_on_config_change", value="1")
        crud.set_setting(session, key="always_send_summary", value="0")
        backup_service.save_diff_rules(
            session,
            [{"scope": "global", "targets": [], "patterns": [r"^ntp clock-period.*"]}],
        )

        device = Device(name="edge-summary-noise", host="10.0.45.2", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        prev_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.finish_backup_record(
            session,
            record_id=prev_record.id,
            success=True,
            config_text="hostname edge-summary-noise\nntp clock-period 11111\n",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        run = crud.create_schedule_run(session, schedule_id=0, trigger="manual", total_devices=1)
        current_record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.add_schedule_run_item(
            session,
            run_id=run.id,
            schedule_id=0,
            backup_id=current_record.id,
            device_id=int(device.id),
        )
        crud.finish_schedule_run(
            session,
            run_id=run.id,
            success_count=1,
            fail_count=0,
            cancelled_count=0,
            error_message=None,
            status=task_state_service.SCHEDULE_RUN_STATUS_SUCCEEDED,
            unfinished_count=0,
        )
        crud.finish_backup_record(
            session,
            record_id=current_record.id,
            success=True,
            config_text="hostname edge-summary-noise\nntp clock-period 22222\n",
            error_message=None,
            duration_seconds=1.0,
            failure_type=None,
        )

        alert_service.check_and_alert_batch(session, run.id)

        assert sent == []


def test_list_device_backups_payload_includes_status_metadata(sqlite_session_factory):
    with sqlite_session_factory() as session:
        device = Device(name="edge-status", host="10.0.50.1", platform="cisco_ios")
        session.add(device)
        session.commit()
        session.refresh(device)

        record = crud.create_backup_record(session, device_id=int(device.id), template_id=None)
        crud.update_backup_record_status(
            session,
            record_id=record.id,
            status=task_state_service.BACKUP_RECORD_STATUS_RUNNING,
        )

        payload = backup_service.list_device_backups_payload(session, device_id=int(device.id))

        assert payload["backups"][0]["status"] == "running"
        assert payload["backups"][0]["status_label"] == "运行中"
        assert payload["backups"][0]["status_tone"] == "running"


def test_schedule_stats_payload_includes_run_status_metadata(sqlite_session_factory):
    with sqlite_session_factory() as session:
        schedule = BackupSchedule(name="nightly-status", crontab="0 1 * * *", enabled=True, targets="all")
        session.add(schedule)
        session.commit()
        session.refresh(schedule)

        run = crud.create_schedule_run(session, schedule_id=int(schedule.id), trigger="manual", total_devices=2)
        crud.update_schedule_run_status(
            session,
            run_id=run.id,
            status=task_state_service.SCHEDULE_RUN_STATUS_FINALIZING,
        )

        payload = schedule_service.get_schedule_stats_payload(session, schedule_id=int(schedule.id))

        assert payload["run_status_labels"][str(run.id)] == "收尾中"
        assert payload["run_status_tones"][str(run.id)] == "running"


def test_schedule_run_error_summary_is_human_readable():
    summary = schedule_service.summarize_schedule_run_error(
        '{"termination_mode":"pending_only","cancelled_backups":3,"unfinished_backups":2,"failures_by_type":{"TIMEOUT":1,"UNKNOWN":2}}'
    )

    assert "已终止 3 个未运行任务" in summary
    assert "仍有 2 个任务执行中" in summary
    assert "超时 1 个" in summary
    assert "未知异常 2 个" in summary


def test_schedule_runs_live_payload_marks_active_runs(sqlite_session_factory):
    with sqlite_session_factory() as session:
        schedule = BackupSchedule(name="nightly-live", crontab="0 1 * * *", enabled=True, targets="all")
        session.add(schedule)
        session.commit()
        session.refresh(schedule)

        run = crud.create_schedule_run(session, schedule_id=int(schedule.id), trigger="manual", total_devices=1)
        crud.update_schedule_run_status(
            session,
            run_id=run.id,
            status=task_state_service.SCHEDULE_RUN_STATUS_RUNNING,
        )

        payload = schedule_service.get_schedule_runs_live_payload(session, schedule_id=int(schedule.id))

        assert payload["has_active_runs"] is True
        assert payload["items"][0]["status"] == "running"
        assert payload["items"][0]["status_label"] == "运行中"
        assert payload["items"][0]["status_tone"] == "running"


def test_schedule_runs_live_payload_includes_human_readable_error_summary(sqlite_session_factory):
    with sqlite_session_factory() as session:
        schedule = BackupSchedule(name="nightly-live-summary", crontab="0 1 * * *", enabled=True, targets="all")
        session.add(schedule)
        session.commit()
        session.refresh(schedule)

        run = crud.create_schedule_run(session, schedule_id=int(schedule.id), trigger="manual", total_devices=3)
        crud.finish_schedule_run(
            session,
            run_id=run.id,
            success_count=1,
            fail_count=0,
            cancelled_count=2,
            error_message='{"termination_mode":"pending_only","cancelled_backups":2}',
            status=task_state_service.SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED,
            unfinished_count=0,
        )

        payload = schedule_service.get_schedule_runs_live_payload(session, schedule_id=int(schedule.id))

        assert payload["items"][0]["status"] == "partial_cancelled"
        assert payload["items"][0]["error_summary"] == "已终止 2 个未运行任务"


def test_schedule_page_payload_uses_shared_pagination_base(sqlite_session_factory):
    with sqlite_session_factory() as session:
        session.add(BackupSchedule(name="nightly-1", crontab="0 1 * * *", enabled=True, targets="all"))
        session.add(BackupSchedule(name="nightly-2", crontab="0 2 * * *", enabled=True, targets="all"))
        session.commit()

        payload = schedule_service.get_schedule_page_payload(
            session,
            page=1,
            limit=10,
            include_limit_param=False,
        )

        assert payload["pagination"]["total"] == 2
        assert payload["pagination"]["total_pages"] == 1
        assert payload["pagination_base"] == "/schedules?page="


def test_expand_group_ids_includes_descendants_for_parent_authorization(sqlite_session_factory):
    with sqlite_session_factory() as session:
        parent = crud.create_group(session, name="总部")
        child = crud.create_group(session, name="核心区", parent_id=int(parent.id))
        leaf = crud.create_group(session, name="接入区", parent_id=int(child.id))

        expanded = crud.expand_group_ids(session, [int(parent.id)])

        assert expanded == [int(parent.id), int(child.id), int(leaf.id)]


def test_get_user_allowed_group_ids_expands_group_tree(sqlite_session_factory):
    from app.routers.support import get_user_allowed_group_ids

    with sqlite_session_factory() as session:
        parent = crud.create_group(session, name="华北")
        child = crud.create_group(session, name="北京", parent_id=int(parent.id))
        user = User(
            username="ops",
            role="operator",
            group_access_type="specific",
            allowed_group_ids=str(int(parent.id)),
            password_hash="x",
        )

        expanded = get_user_allowed_group_ids(user, session=session)

        assert expanded == [int(parent.id), int(child.id)]


def test_normalize_schedule_targets_prefers_group_id_and_keeps_legacy_name_compatible(sqlite_session_factory):
    with sqlite_session_factory() as session:
        parent = crud.create_group(session, name="园区")
        child = crud.create_group(session, name="汇聚", parent_id=int(parent.id))

        normalized = schedule_service.normalize_schedule_targets(
            session,
            targets=f"group:{child.name}\ndevice:12\ngroup:{int(parent.id)}",
        )

        assert normalized.splitlines() == [f"group:{int(child.id)}", "device:12", f"group:{int(parent.id)}"]


def test_list_legacy_group_name_targets_only_reports_named_group_tokens(sqlite_session_factory):
    with sqlite_session_factory() as session:
        group = crud.create_group(session, name="核心网络")
        session.add(
            BackupSchedule(
                name="nightly-legacy",
                crontab="0 1 * * *",
                enabled=True,
                targets=f"group:{group.name}\nhost:10.0.0.1\ndevice:2",
            )
        )
        session.commit()

        legacy_items = schedule_service.list_legacy_group_name_targets(session)

        assert len(legacy_items) == 1
        assert legacy_items[0]["targets"] == [
            {"raw": f"group:{group.name}", "normalized": f"group:{int(group.id)}"}
        ]


def test_devices_page_payload_preserves_filters_in_pagination_base(sqlite_session_factory):
    with sqlite_session_factory() as session:
        session.add(Device(name="edge-1", host="10.0.0.1", platform="cisco_ios"))
        session.commit()

        filters = device_service.normalize_list_filters(q="edge", platform="cisco_ios")
        payload = device_service.get_devices_page_payload(
            session,
            filters=filters,
            page=1,
            page_size=10,
            include_limit_param=False,
        )

        assert payload["pagination"]["total"] == 1
        assert "q=edge" in payload["pagination_base"]
        assert "platform=cisco_ios" in payload["pagination_base"]
        assert "limit=10" not in payload["pagination_base"]


def test_device_detail_payload_uses_shared_pagination_base(sqlite_session_factory):
    with sqlite_session_factory() as session:
        device = Device(name="core-1", host="10.0.0.2", platform="huawei")
        session.add(device)
        session.commit()
        session.refresh(device)

        payload = device_service.get_device_detail_page_payload(
            session,
            device_id=int(device.id),
            page=1,
            page_size=10,
            include_limit_param=False,
        )

        assert payload["pagination"]["total"] == 0
        assert payload["pagination_base"] == f"/devices/{int(device.id)}?page="


def test_run_backup_execution_calls_backup_device(monkeypatch):
    context = task_execution_service.BackupExecutionContext(
        record_id=uuid4(),
        device_id=1,
        task_id="task-1",
        request_id="req-1",
        device_name="edge-1",
        device_host="10.0.0.1",
        device_port=22,
        device_login_method="ssh",
        device_encoding="utf-8",
        device_platform="cisco_ios",
        effective_template_id=None,
        template_commands="show running-config",
        secrets={"username": "admin", "password": "pwd", "enable_password": "enable"},
    )
    backup_mock = Mock(return_value="config-data")
    monkeypatch.setattr(task_execution_service, "backup_device", backup_mock)

    result = task_execution_service.run_backup_execution(context)

    assert result.config_text == "config-data"
    backup_mock.assert_called_once_with(
        host="10.0.0.1",
        port=22,
        login_method="ssh",
        encoding="utf-8",
        platform="cisco_ios",
        username="admin",
        password="pwd",
        enable_password="enable",
        template_commands="show running-config",
    )


def test_finalize_backup_execution_returns_retry_instruction():
    context = task_execution_service.BackupExecutionContext(
        record_id=uuid4(),
        device_id=2,
        task_id="task-2",
        request_id="req-2",
        device_name="core-1",
        device_host="10.0.0.2",
        device_port=22,
        device_login_method="ssh",
        device_encoding="utf-8",
        device_platform="huawei",
        effective_template_id=None,
        template_commands=None,
        secrets={"username": "admin", "password": "pwd", "enable_password": None},
    )

    result = task_execution_service.finalize_backup_execution(
        context=context,
        skip_email=True,
        duration=1.25,
        error=TimeoutError("timeout"),
        retries_done=1,
        max_retries=3,
        backoff_base=10,
        is_retryable_failure=lambda exc, failure_type: True,
        build_retry_countdown=lambda retry_index, backoff: 20,
    )

    assert result.should_retry is True
    assert result.retry_countdown == 20
    assert result.response["failure_type"] == "UNKNOWN"


def test_api_keys_page_payload_uses_shared_pagination(sqlite_session_factory):
    with sqlite_session_factory() as session:
        for i in range(12):
            api_key_management_service.create_api_key(
                session,
                name=f"key-{i}",
                created_by=None,
                expires_in_days=0,
            )

        payload = api_key_management_service.get_api_keys_page_payload(
            session,
            page=2,
            limit=10,
            limit_in_query=False,
        )

        assert len(payload["api_keys"]) == 2
        assert payload["pagination"]["page"] == 2
        assert payload["pagination"]["total_pages"] == 2
        assert payload["pagination_base"] == "/api-keys?page="


def test_device_list_query_input_parses_filters_and_pagination():
    query = DeviceListQueryInput.from_query_params(
        {
            "q": " edge ",
            "login_method": "ssh",
            "platform": "cisco_ios",
            "group_id": "3",
            "status": "online",
            "page": "2",
            "limit": "50",
        }
    )

    assert query.q == " edge "
    assert query.group_id == "3"
    assert query.page == "2"
    assert query.limit == "50"
    assert query.include_limit_param is True


def test_base_list_query_input_supports_custom_default_limit():
    query = BaseListQueryInput.from_query_params({}, default_limit=50)

    assert query.page == 1
    assert query.limit == 50
    assert query.include_limit_param is False


def test_device_host_port_combo_is_unique(sqlite_session_factory):
    with sqlite_session_factory() as session:
        credential = Credential(name="cred-host-port", username="admin")
        session.add(credential)
        session.commit()
        session.refresh(credential)

        first = device_service.create_device(
            session,
            DeviceCreateInput(
                name="edge-host-port-1",
                host="10.0.99.1",
                port=22,
                platform="cisco_ios",
                credential_id=int(credential.id),
            ),
        )
        second = device_service.create_device(
            session,
            DeviceCreateInput(
                name="edge-host-port-2",
                host="10.0.99.1",
                port=23,
                platform="cisco_ios",
                credential_id=int(credential.id),
            ),
        )

        assert first.id is not None
        assert second.id is not None

        with pytest.raises(device_service.ServiceError) as exc_info:
            device_service.create_device(
                session,
                DeviceCreateInput(
                    name="edge-host-port-3",
                    host="10.0.99.1",
                    port=23,
                    platform="cisco_ios",
                    credential_id=int(credential.id),
                ),
            )

        assert exc_info.value.code == "DEVICE_HOST_EXISTS"


def test_editable_list_query_input_parses_edit_and_pagination():
    query = EditableListQueryInput.from_query_params(
        {
            "edit": "12",
            "page": "3",
            "limit": "20",
        }
    )

    assert query.edit == "12"
    assert query.page == "3"
    assert query.limit == "20"
    assert query.include_limit_param is True


def test_audit_and_config_search_query_inputs_parse_filters():
    audit_query = AuditLogListQueryInput.from_query_params(
        {
            "q": "admin",
            "action": "LOGIN",
            "resource_type": "user",
            "page": "2",
        }
    )
    config_query = ConfigSearchListQueryInput.from_query_params(
        {
            "q": "hostname",
            "scope": "all",
        }
    )

    assert audit_query.q == "admin"
    assert audit_query.action == "LOGIN"
    assert audit_query.resource_type == "user"
    assert audit_query.page == "2"
    assert config_query.q == "hostname"
    assert config_query.scope == "all"
    assert config_query.limit == 50
