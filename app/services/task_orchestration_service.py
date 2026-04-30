from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError
from sqlmodel import Session

from app import crud
from app.core.settings import settings
from app.platforms import platforms_compatible
from app.services.alert_service import check_and_alert_batch
from app.services import task_dispatcher_service, task_runtime_config_service, task_state_service
from app.services.errors import ServiceError


BackupDispatchJob = tuple[int, UUID, int | None]


@dataclass(slots=True, frozen=True)
class PlannedBackupRecord:
    record_id: UUID
    device_id: int
    started_at: datetime
    template_id: int | None


@dataclass(slots=True, frozen=True)
class ScheduleRunFinalization:
    should_retry: bool
    retry_countdown: int | None
    response: dict[str, object]
    success_count: int = 0
    fail_count: int = 0


@dataclass(slots=True, frozen=True)
class TerminateScheduleRunResult:
    run_id: UUID
    schedule_id: int
    status: str
    terminated_records: int
    skipped_records: int
    running_records: int
    message: str


def _resolve_effective_template_id(session: Session, *, device_id: int) -> int | None:
    device = crud.get_device(session, device_id)
    if device is None:
        return None
    effective_template_id = int(getattr(device, "default_template_id", 0) or 0)
    if not effective_template_id:
        return None
    template = crud.get_template(session, effective_template_id)
    if template is None:
        return None
    if not platforms_compatible(template.platform, device.platform):
        return None
    return int(effective_template_id)


def _mark_backup_record_failed_if_unfinished(
    session: Session,
    record_id: UUID,
    *,
    error_message: str,
    failure_type: str,
) -> bool:
    record = crud.get_backup(session, record_id)
    if record is None or task_state_service.is_backup_record_terminal_status(record.status):
        return False
    crud.finish_backup_record(
        session,
        record_id=record_id,
        success=False,
        config_text=None,
        error_message=(error_message or "").strip() or "Task failed",
        duration_seconds=None,
        failure_type=(failure_type or "").strip() or "UNKNOWN",
    )
    return True


def _finish_schedule_run_enqueue_failed(
    session: Session,
    *,
    run_id: UUID,
    fail_count: int,
    error_payload: dict[str, object],
) -> None:
    run = crud.get_schedule_run(session, run_id)
    if run is None or task_state_service.is_schedule_run_terminal_status(run.status):
        return
    crud.finish_schedule_run(
        session,
        run_id=run_id,
        success_count=0,
        fail_count=int(fail_count or 0),
        error_message=json.dumps(error_payload, ensure_ascii=False),
        status=task_state_service.SCHEDULE_RUN_STATUS_FAILED,
    )


def _revoke_backup_task(record_id: UUID) -> None:
    from app.celery_app import celery_app

    celery_app.control.revoke(str(record_id))


def _summarize_schedule_run_records(records: list[object]) -> tuple[int, int, int, int, dict[str, int]]:
    success_count = 0
    fail_count = 0
    cancelled_count = 0
    unfinished_count = 0
    failures_by_type: dict[str, int] = {}

    for record in records:
        status = str(getattr(record, "status", "") or "").strip()
        if task_state_service.is_backup_record_pending_status(status) or status == task_state_service.BACKUP_RECORD_STATUS_RUNNING:
            unfinished_count += 1
            continue
        if status == task_state_service.BACKUP_RECORD_STATUS_CANCELLED:
            cancelled_count += 1
            continue
        if getattr(record, "success", False):
            success_count += 1
            continue
        fail_count += 1
        failure_type = str(getattr(record, "failure_type", None) or "UNKNOWN")
        failures_by_type[failure_type] = failures_by_type.get(failure_type, 0) + 1

    return success_count, fail_count, cancelled_count, unfinished_count, failures_by_type


def terminate_schedule_run(
    session: Session,
    *,
    run_id: UUID,
) -> TerminateScheduleRunResult:
    run = crud.get_schedule_run(session, run_id)
    if run is None:
        raise ServiceError("运行记录不存在", code="SCHEDULE_RUN_NOT_FOUND", status_code=404)
    if task_state_service.is_schedule_run_terminal_status(run.status):
        return TerminateScheduleRunResult(
            run_id=run_id,
            schedule_id=int(run.schedule_id or 0),
            status=str(run.status or ""),
            terminated_records=0,
            skipped_records=int(run.total_devices or 0),
            running_records=0,
            message="该运行已结束，无需终止",
        )

    items = crud.list_schedule_run_items(session, run_id)
    backup_ids = [item.backup_id for item in items if item.backup_id]
    records = crud.list_backups_by_ids(session, backup_ids)
    by_id = {record.id: record for record in records if getattr(record, "id", None)}

    pending_records = [
        record
        for record in records
        if task_state_service.is_backup_record_pending_status(getattr(record, "status", None))
    ]
    running_records = [
        record
        for record in records
        if str(getattr(record, "status", "") or "").strip() == task_state_service.BACKUP_RECORD_STATUS_RUNNING
    ]

    if not pending_records:
        return TerminateScheduleRunResult(
            run_id=run_id,
            schedule_id=int(run.schedule_id or 0),
            status=str(run.status or ""),
            terminated_records=0,
            skipped_records=len(records),
            running_records=len(running_records),
            message="当前没有可终止的未运行任务",
        )

    for record in pending_records:
        try:
            _revoke_backup_task(record.id)
        except Exception:
            # Even if revoke fails, a cancelled DB status prevents execution from proceeding.
            pass
        crud.cancel_backup_record(
            session,
            record_id=record.id,
            error_message="未运行任务已被人工终止",
            failure_type="CANCELLED",
        )
        by_id[record.id] = crud.get_backup(session, record.id)

    updated_records = [by_id.get(backup_id) for backup_id in backup_ids]
    materialized_records = [record for record in updated_records if record is not None]
    success_count, fail_count, cancelled_count, unfinished_count, failures_by_type = _summarize_schedule_run_records(
        materialized_records
    )
    error_payload: dict[str, object] = {
        "termination_mode": "pending_only",
        "cancelled_backups": int(cancelled_count),
    }
    if failures_by_type:
        error_payload["failures_by_type"] = failures_by_type

    if unfinished_count <= 0:
        final_status = task_state_service.derive_schedule_run_terminal_status(
            total_devices=int(run.total_devices or 0),
            success_count=success_count,
            fail_count=fail_count,
            cancelled_count=cancelled_count,
            unfinished_count=0,
        )
        crud.finish_schedule_run(
            session,
            run_id=run_id,
            success_count=success_count,
            fail_count=fail_count,
            cancelled_count=cancelled_count,
            error_message=json.dumps(error_payload, ensure_ascii=False),
            status=final_status,
            unfinished_count=0,
        )
        return TerminateScheduleRunResult(
            run_id=run_id,
            schedule_id=int(run.schedule_id or 0),
            status=final_status,
            terminated_records=len(pending_records),
            skipped_records=max(0, len(records) - len(pending_records)),
            running_records=0,
            message="未运行任务已终止",
        )

    crud.update_schedule_run_status(
        session,
        run_id=run_id,
        status=task_state_service.SCHEDULE_RUN_STATUS_CANCELLING,
    )
    return TerminateScheduleRunResult(
        run_id=run_id,
        schedule_id=int(run.schedule_id or 0),
        status=task_state_service.SCHEDULE_RUN_STATUS_CANCELLING,
        terminated_records=len(pending_records),
        skipped_records=max(0, len(records) - len(pending_records)),
        running_records=len(running_records),
        message="未运行任务已终止，运行中的任务将继续完成",
    )


def plan_single_backup(
    session: Session,
    *,
    device_id: int,
    template_id: int = 0,
) -> PlannedBackupRecord:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError(
            "Device not found",
            code="BACKUP_DEVICE_NOT_FOUND",
            status_code=404,
            context={"device_id": device_id},
        )

    effective_template_id = int(template_id or 0) or int(getattr(device, "default_template_id", 0) or 0)
    if effective_template_id:
        template = crud.get_template(session, effective_template_id)
        if template is None:
            raise ServiceError(
                "Template not found",
                code="BACKUP_TEMPLATE_NOT_FOUND",
                status_code=400,
                context={"template_id": effective_template_id},
            )
        if not platforms_compatible(template.platform, device.platform):
            raise ServiceError(
                "Template platform mismatch",
                code="BACKUP_TEMPLATE_PLATFORM_MISMATCH",
                status_code=400,
                context={"template_id": effective_template_id, "device_id": device_id},
            )

    record = crud.create_backup_record(
        session,
        device_id=device_id,
        template_id=effective_template_id or None,
    )
    return PlannedBackupRecord(
        record_id=record.id,
        device_id=int(record.device_id),
        started_at=record.started_at,
        template_id=effective_template_id or None,
    )


def plan_device_batch_run(
    session: Session,
    *,
    device_ids: list[int],
    trigger: str = "manual",
    schedule_id: int = 0,
) -> tuple[UUID, list[BackupDispatchJob]]:
    run = crud.create_schedule_run(session, schedule_id=int(schedule_id), trigger=trigger, total_devices=len(device_ids))
    run_id = UUID(str(run.id))
    jobs: list[BackupDispatchJob] = []
    for device_id in device_ids:
        template_id = _resolve_effective_template_id(session, device_id=device_id)
        record = crud.create_backup_record(session, device_id=device_id, template_id=template_id)
        crud.add_schedule_run_item(
            session,
            run_id=run_id,
            schedule_id=int(schedule_id),
            backup_id=record.id,
            device_id=device_id,
        )
        jobs.append((int(device_id), record.id, template_id))
    return run_id, jobs


def plan_schedule_run(
    session: Session,
    *,
    schedule_id: int,
    trigger: str,
    device_ids: list[int],
) -> tuple[UUID, list[BackupDispatchJob]]:
    schedule = crud.get_schedule(session, schedule_id)
    if schedule is None:
        raise RuntimeError("Schedule not found")
    return plan_device_batch_run(
        session,
        device_ids=device_ids,
        trigger=trigger,
        schedule_id=int(schedule_id),
    )


def enqueue_single_backup(
    session: Session,
    *,
    planned: PlannedBackupRecord,
    skip_email: bool,
) -> bool:
    from app.celery_tasks import backup_record_task, celery_enabled

    crud.update_backup_record_status(
        session,
        record_id=planned.record_id,
        status=task_state_service.BACKUP_RECORD_STATUS_QUEUED,
    )
    # Make the record visible before dispatching to a different process.
    session.commit()

    if not celery_enabled():
        _mark_backup_record_failed_if_unfinished(
            session,
            planned.record_id,
            error_message="Celery 未启用或不可用",
            failure_type="ENQUEUE_FAILED",
        )
        return False

    try:
        task_dispatcher_service.dispatch_backup_record_task(
            backup_record_task,
            record_id=planned.record_id,
            device_id=planned.device_id,
            template_id=planned.template_id,
            skip_email=skip_email,
            time_limit=task_runtime_config_service.load_task_time_limit(),
        )
        return True
    except (CeleryError, OperationalError, OSError, TypeError, ValueError) as exc:
        _mark_backup_record_failed_if_unfinished(
            session,
            planned.record_id,
            error_message=f"任务入队失败: {str(exc)}",
            failure_type="ENQUEUE_FAILED",
        )
        return False


def enqueue_schedule_run(
    session: Session,
    *,
    run_id: UUID,
    jobs: list[BackupDispatchJob],
    skip_email: bool = True,
) -> bool:
    from app.celery_tasks import backup_record_task, celery_enabled, finalize_schedule_run_task

    crud.update_schedule_run_status(
        session,
        run_id=run_id,
        status=task_state_service.SCHEDULE_RUN_STATUS_DISPATCHING,
    )
    for _, backup_id, __ in jobs:
        crud.update_backup_record_status(
            session,
            record_id=backup_id,
            status=task_state_service.BACKUP_RECORD_STATUS_QUEUED,
        )
    # Make schedule-run and backup records visible before workers load them.
    session.commit()

    if not celery_enabled():
        for _, backup_id, __ in jobs:
            _mark_backup_record_failed_if_unfinished(
                session,
                backup_id,
                error_message="Celery 未启用或不可用",
                failure_type="ENQUEUE_FAILED",
            )
        _finish_schedule_run_enqueue_failed(
            session,
            run_id=run_id,
            fail_count=len(jobs),
            error_payload={"enqueue_error": "CELERY_UNAVAILABLE"},
        )
        return False

    backup_ids = [str(backup_id) for _, backup_id, __ in jobs]
    enqueued_backup_ids: set[str] = set()
    try:
        poll = max(1, int(settings.celery.schedule_finalize_poll_seconds or 5))
        time_limit = task_runtime_config_service.load_task_time_limit()

        # Enqueue finalizer first so partial backup dispatch failures still have a
        # collector to close the run after failed records are written back.
        task_dispatcher_service.dispatch_schedule_finalizer(
            finalize_schedule_run_task,
            run_id=run_id,
            backup_ids=backup_ids,
            countdown=poll,
        )

        for device_id, backup_id, template_id in jobs:
            task_dispatcher_service.dispatch_backup_record_task(
                backup_record_task,
                record_id=backup_id,
                device_id=device_id,
                template_id=template_id,
                skip_email=skip_email,
                time_limit=time_limit,
            )
            enqueued_backup_ids.add(str(backup_id))
        crud.update_schedule_run_status(
            session,
            run_id=run_id,
            status=task_state_service.SCHEDULE_RUN_STATUS_RUNNING,
        )
        return True
    except (CeleryError, OperationalError, OSError, TypeError, ValueError) as exc:
        for _, backup_id, __ in jobs:
            if str(backup_id) in enqueued_backup_ids:
                continue
            _mark_backup_record_failed_if_unfinished(
                session,
                backup_id,
                error_message=f"任务入队失败: {str(exc)}",
                failure_type="ENQUEUE_FAILED",
            )
        if not enqueued_backup_ids:
            _finish_schedule_run_enqueue_failed(
                session,
                run_id=run_id,
                fail_count=len(jobs),
                error_payload={"enqueue_error": str(exc)},
            )
        else:
            crud.update_schedule_run_status(
                session,
                run_id=run_id,
                status=task_state_service.SCHEDULE_RUN_STATUS_RUNNING,
            )
        return False


def finalize_schedule_run(
    session: Session,
    *,
    run_id: UUID,
    backup_ids: list[UUID],
    retries_done: int,
) -> ScheduleRunFinalization:
    if not backup_ids:
        crud.finish_schedule_run(
            session,
            run_id=run_id,
            success_count=0,
            fail_count=0,
            error_message=None,
            status=task_state_service.SCHEDULE_RUN_STATUS_SUCCEEDED,
        )
        return ScheduleRunFinalization(
            should_retry=False,
            retry_countdown=None,
            response={"ok": True, "run_id": str(run_id), "reason": "no_jobs"},
            success_count=0,
            fail_count=0,
        )

    run = crud.get_schedule_run(session, run_id)
    if run is None:
        return ScheduleRunFinalization(
            should_retry=False,
            retry_countdown=None,
            response={"ok": False, "run_id": str(run_id), "reason": "run_not_found"},
        )
    if task_state_service.is_schedule_run_terminal_status(run.status):
        return ScheduleRunFinalization(
            should_retry=False,
            retry_countdown=None,
            response={"ok": True, "run_id": str(run_id), "reason": "already_finished"},
            success_count=int(run.success_count or 0),
            fail_count=int(run.fail_count or 0),
        )

    if run.status != task_state_service.SCHEDULE_RUN_STATUS_CANCELLING:
        crud.update_schedule_run_status(
            session,
            run_id=run_id,
            status=task_state_service.SCHEDULE_RUN_STATUS_FINALIZING,
        )

    records = crud.list_backups_by_ids(session, backup_ids)
    finished = [record for record in records if task_state_service.is_backup_record_terminal_status(record.status)]
    max_polls = int(settings.celery.schedule_finalize_max_polls or 0)
    if len(finished) < len(backup_ids) and retries_done < max_polls:
        poll = max(1, int(settings.celery.schedule_finalize_poll_seconds or 5))
        return ScheduleRunFinalization(
            should_retry=True,
            retry_countdown=poll,
            response={"ok": False, "run_id": str(run_id), "reason": "pending"},
        )

    success_count, fail_count, cancelled_count, unfinished_count, failures_by_type = _summarize_schedule_run_records(records)

    error_payload: dict[str, object] = {}
    if unfinished_count > 0:
        error_payload["unfinished_backups"] = int(unfinished_count)
    if failures_by_type:
        error_payload["failures_by_type"] = failures_by_type
    if cancelled_count > 0:
        error_payload["cancelled_backups"] = int(cancelled_count)
        error_payload["termination_mode"] = "pending_only"

    final_status = task_state_service.derive_schedule_run_terminal_status(
        total_devices=int(run.total_devices or 0),
        success_count=success_count,
        fail_count=fail_count,
        cancelled_count=cancelled_count,
        unfinished_count=unfinished_count,
    )

    crud.finish_schedule_run(
        session,
        run_id=run_id,
        success_count=success_count,
        fail_count=fail_count,
        cancelled_count=cancelled_count,
        error_message=json.dumps(error_payload, ensure_ascii=False) if error_payload else None,
        status=final_status,
        unfinished_count=unfinished_count,
    )
    check_and_alert_batch(session, run_id)

    retention_days_str = crud.get_setting(session, key="backup_retention_days")
    try:
        retention_days = int(retention_days_str or "90")
    except (TypeError, ValueError):
        retention_days = 90
    if retention_days > 0:
        crud.cleanup_old_backups(session, retention_days)
    crud.cleanup_old_task_events(session, 90)

    return ScheduleRunFinalization(
        should_retry=False,
        retry_countdown=None,
        response={
            "ok": True,
            "run_id": str(run_id),
            "success": int(success_count),
            "fail": int(fail_count),
            "cancelled": int(cancelled_count),
            "status": final_status,
            "finished_at": datetime.utcnow().isoformat(),
        },
        success_count=success_count,
        fail_count=fail_count,
    )
