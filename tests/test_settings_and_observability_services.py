from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import crud
from app.models import BackupRecord, BackupScheduleRun, Device, TaskEvent
from app.services import settings_service, task_observability_service, task_state_service


@pytest.fixture()
def sqlite_session_factory(tmp_path):
    db_path = tmp_path / "test_services.sqlite"
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


def test_save_system_settings_normalizes_values_and_persists(sqlite_session_factory):
    with sqlite_session_factory() as session:
        payload = settings_service.save_system_settings(
            session,
            timezone_offset="invalid",
            max_concurrent_tasks="-5",
            backup_max_retries="99",
            backup_retry_backoff="0",
            task_time_limit="-1",
            backup_retention_days="abc",
            webshell_record_retention_days="0",
            audit_log_retention_days="-9",
            login_log_retention_days="365",
        )

        assert payload.timezone_offset == "+08:00"
        assert payload.max_concurrent_tasks == "1"
        assert payload.backup_max_retries == "10"
        assert payload.backup_retry_backoff == "1"
        assert payload.task_time_limit == "0"
        assert payload.backup_retention_days == "90"
        assert payload.webshell_record_retention_days == "1"
        assert payload.audit_log_retention_days == "1"
        assert payload.login_log_retention_days == "365"

        persisted = settings_service.get_system_settings_payload(session)
        assert persisted.as_dict() == payload.as_dict()
        assert crud.get_setting(session, key="task_time_limit") == "0"


def test_task_health_snapshot_includes_duration_platform_and_device_trends(sqlite_session_factory):
    now = datetime.utcnow()

    with sqlite_session_factory() as session:
        device_ok = Device(name="edge-1", host="10.0.0.1", platform="cisco_ios")
        device_fail = Device(name="edge-2", host="10.0.0.2", platform="cisco_ios")
        device_other = Device(name="core-1", host="10.0.0.3", platform="huawei")
        session.add(device_ok)
        session.add(device_fail)
        session.add(device_other)
        session.commit()
        session.refresh(device_ok)
        session.refresh(device_fail)
        session.refresh(device_other)

        session.add(
            BackupRecord(
                id=uuid4(),
                device_id=int(device_ok.id),
                status=task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED,
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(minutes=50),
                success=True,
                duration_seconds=10.0,
            )
        )
        session.add(
            BackupRecord(
                id=uuid4(),
                device_id=int(device_fail.id),
                status=task_state_service.BACKUP_RECORD_STATUS_FAILED,
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1, minutes=45),
                success=False,
                failure_type="TIMEOUT",
                duration_seconds=20.0,
            )
        )
        session.add(
            BackupRecord(
                id=uuid4(),
                device_id=int(device_other.id),
                status=task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED,
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2, minutes=30),
                success=True,
                duration_seconds=40.0,
            )
        )
        session.add(
            BackupRecord(
                id=uuid4(),
                device_id=int(device_other.id),
                status=task_state_service.BACKUP_RECORD_STATUS_RUNNING,
                started_at=now - timedelta(minutes=10),
                finished_at=None,
                success=False,
            )
        )
        session.add(
            BackupRecord(
                id=uuid4(),
                device_id=int(device_ok.id),
                status=task_state_service.BACKUP_RECORD_STATUS_RUNNING,
                started_at=now - timedelta(hours=1),
                finished_at=None,
                success=False,
            )
        )
        session.add(
            BackupScheduleRun(
                id=uuid4(),
                schedule_id=1,
                status=task_state_service.SCHEDULE_RUN_STATUS_RUNNING,
                started_at=now - timedelta(minutes=5),
                finished_at=None,
            )
        )
        session.add(
            TaskEvent(
                event="backup_record_task_retry_scheduled",
                task_id=str(uuid4()),
                record_id=str(uuid4()),
                device_id=int(device_fail.id),
                failure_type="TIMEOUT",
                retries_done=1,
                max_retries=3,
                created_at=now - timedelta(hours=1, minutes=30),
            )
        )
        session.add(
            TaskEvent(
                event="backup_record_storage_upload",
                task_id=str(uuid4()),
                record_id=str(uuid4()),
                device_id=int(device_ok.id),
                storage_type="S3",
                success=True,
                created_at=now - timedelta(minutes=40),
            )
        )
        session.add(
            TaskEvent(
                event="backup_record_storage_upload",
                task_id=str(uuid4()),
                record_id=str(uuid4()),
                device_id=int(device_other.id),
                storage_type="FTP",
                success=False,
                created_at=now - timedelta(minutes=35),
            )
        )
        session.commit()

        snapshot = task_observability_service.get_task_health_snapshot(session, now=now)

        assert snapshot["recent_total"] == 5
        assert snapshot["recent_failed"] == 1
        assert snapshot["running_count"] == 2
        assert snapshot["active_schedule_runs"] == 1
        assert snapshot["retry_scheduled_count"] == 1
        assert snapshot["avg_duration_seconds"] == 23.33
        assert snapshot["max_duration_seconds"] == 40.0
        assert snapshot["upload_attempt_total"] == 2
        assert snapshot["upload_success_total"] == 1
        assert snapshot["upload_success_rate"] == 50.0

        platform_rows = {item["platform"]: item for item in snapshot["platform_success_trends"]}
        assert platform_rows["cisco_ios"]["total"] == 2
        assert platform_rows["cisco_ios"]["success_count"] == 1
        assert platform_rows["cisco_ios"]["success_rate"] == 50.0

        upload_rows = {item["storage_type"]: item for item in snapshot["storage_uploads"]}
        assert upload_rows["S3"]["success_rate"] == 100.0
        assert upload_rows["FTP"]["success_rate"] == 0.0

        device_rows = {item["device_name"]: item for item in snapshot["device_success_trends"]}
        assert device_rows["edge-2"]["fail_count"] == 1
        assert device_rows["edge-2"]["success_rate"] == 0.0
        assert device_rows["core-1"]["success_rate"] == 100.0


def test_cleanup_old_task_events_keeps_recent_90_days_by_default(sqlite_session_factory):
    now = datetime.utcnow()

    with sqlite_session_factory() as session:
        session.add(
            TaskEvent(
                event="backup_record_task_started",
                task_id=str(uuid4()),
                created_at=now - timedelta(days=91),
            )
        )
        session.add(
            TaskEvent(
                event="backup_record_task_started",
                task_id=str(uuid4()),
                created_at=now - timedelta(days=89),
            )
        )
        session.commit()

        deleted = crud.cleanup_old_task_events(session)

        assert deleted == 1
        remaining = session.exec(select(TaskEvent)).all()
        assert len(remaining) == 1
        assert remaining[0].created_at >= now - timedelta(days=90)
