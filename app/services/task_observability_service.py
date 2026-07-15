from __future__ import annotations

import atexit
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db import session_scope
from app.i18n import translate
from app.models import BackupRecord, BackupScheduleRun, Device, TaskEvent
from app.services import task_event_bus_service, task_runtime_config_service, task_state_service

_MODULE_LOGGER = logging.getLogger(__name__)
_SHUTDOWN_LOGGER = logging.getLogger(f"{__name__}.shutdown")
_SHUTDOWN_LOGGER.addHandler(logging.NullHandler())
_SHUTDOWN_LOGGER.propagate = False
_TASK_EVENT_BUFFER_BATCH_SIZE = 20
_TASK_EVENT_BUFFER_FLUSH_INTERVAL_SECONDS = 5.0
_TASK_EVENT_BUFFER_MAX_SIZE = 500
_TASK_EVENT_IMMEDIATE_PERSIST_EVENTS = {
    "backup_record_netmiko_connecting",
    "backup_record_netmiko_connected",
    "backup_record_enable_started",
    "backup_record_enable_completed",
    "backup_record_prompt_detected",
    "backup_record_command_started",
    "backup_record_command_pagination_detected",
    "backup_record_command_pagination_completed",
    "backup_record_command_completed",
    "backup_record_netmiko_disconnected",
    "backup_record_legacy_ssh_fallback_started",
    "backup_record_legacy_ssh_fallback_completed",
    "backup_record_netmiko_failed",
    "backup_record_netmiko_completed",
    "backup_record_task_retry_scheduled",
    "backup_record_storage_upload",
    "backup_record_task_signal_failure",
    "backup_record_task_revoked",
    "backup_record_semaphore_degraded",
}
_TASK_EVENT_LOG_ONLY_EVENTS = {
    "bulk_reachability_device_error",
}
_TASK_EVENT_BUFFER_LOCK = threading.Lock()
_TASK_EVENT_BUFFER: list["BufferedTaskEvent"] = []
_LAST_TASK_EVENT_FLUSH_AT = monotonic()


@dataclass(frozen=True, slots=True)
class BufferedTaskEvent:
    event: str
    task_id: str | None
    record_id: str | None
    run_id: str | None
    request_id: str | None
    failure_type: str | None
    extra: dict[str, Any]


def log_task_event(
    logger: logging.Logger,
    *,
    level: str,
    event: str,
    task_id: str | None = None,
    record_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    failure_type: str | None = None,
    persist: bool = True,
    **extra: Any,
) -> None:
    payload = {
        "event": event,
        "task_id": task_id or "",
        "record_id": record_id or "",
        "run_id": run_id or "",
        "request_id": request_id or "",
        "failure_type": failure_type or "",
        **extra,
    }
    log_method = getattr(logger, level, logger.info)
    log_method(json.dumps(payload, ensure_ascii=False))
    if not persist:
        return

    buffered_event = BufferedTaskEvent(
        event=event,
        task_id=task_id,
        record_id=record_id,
        run_id=run_id,
        request_id=request_id,
        failure_type=failure_type,
        extra=dict(extra),
    )
    persistence_mode = _resolve_task_event_persistence_mode(event)
    if persistence_mode == "log_only":
        return
    if persistence_mode == "immediate":
        flush_task_event_buffer(logger=logger, force=True)
        _persist_task_events([buffered_event], logger=logger, flush_reason="immediate", allow_requeue=False)
        return
    _buffer_task_event(buffered_event, logger=logger)


def flush_task_event_buffer(
    *,
    logger: logging.Logger | None = None,
    force: bool = False,
    allow_requeue: bool = True,
) -> int:
    global _LAST_TASK_EVENT_FLUSH_AT

    selected_logger = logger or _MODULE_LOGGER
    now = monotonic()
    pending_events: list[BufferedTaskEvent] = []

    with _TASK_EVENT_BUFFER_LOCK:
        if not _TASK_EVENT_BUFFER:
            return 0
        if not force:
            should_flush = (
                len(_TASK_EVENT_BUFFER) >= _TASK_EVENT_BUFFER_BATCH_SIZE
                or (now - _LAST_TASK_EVENT_FLUSH_AT) >= _TASK_EVENT_BUFFER_FLUSH_INTERVAL_SECONDS
            )
            if not should_flush:
                return 0
        pending_events = list(_TASK_EVENT_BUFFER)
        _TASK_EVENT_BUFFER.clear()
        _LAST_TASK_EVENT_FLUSH_AT = now

    _persist_task_events(
        pending_events,
        logger=selected_logger,
        flush_reason="forced" if force else "buffered",
        allow_requeue=allow_requeue,
    )
    return len(pending_events)


def get_task_health_snapshot(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
    locale: str | None = None,
) -> dict[str, Any]:
    flush_task_event_buffer(logger=_MODULE_LOGGER, force=True)

    current = now or datetime.utcnow()
    normalized_window_hours = max(1, int(window_hours))
    recent_threshold = current - timedelta(hours=normalized_window_hours)
    stale_threshold = current - timedelta(minutes=30)

    backup_counts = session.exec(
        select(
            func.sum(case((BackupRecord.started_at >= recent_threshold, 1), else_=0)).label("recent_total"),
            func.sum(
                case(
                    (
                        (
                            (BackupRecord.started_at >= recent_threshold)
                            & (BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_FAILED)
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("recent_failed"),
            func.sum(case((BackupRecord.status.in_(task_state_service.BACKUP_RECORD_ACTIVE_STATUSES), 1), else_=0)).label("running_count"),
            func.sum(
                case(
                    (
                        (
                            BackupRecord.status.in_(task_state_service.BACKUP_RECORD_ACTIVE_STATUSES)
                            & (BackupRecord.started_at < stale_threshold)
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("stale_running_count"),
        )
        .select_from(BackupRecord)
    ).one()
    recent_total = int(backup_counts[0] or 0)
    recent_failed = int(backup_counts[1] or 0)
    running_count = int(backup_counts[2] or 0)
    stale_running_count = int(backup_counts[3] or 0)
    active_schedule_runs = int(
        session.exec(
            select(func.count())
            .select_from(BackupScheduleRun)
            .where(BackupScheduleRun.status.in_(task_state_service.SCHEDULE_RUN_ACTIVE_STATUSES))
        ).one()
    )
    retry_scheduled_count = int(
        session.exec(
            select(func.count())
            .select_from(TaskEvent)
            .where(
                TaskEvent.event == "backup_record_task_retry_scheduled",
                TaskEvent.created_at >= recent_threshold,
            )
        ).one()
    )
    duration_stats = session.exec(
        select(
            func.avg(BackupRecord.duration_seconds).label("avg_duration_seconds"),
            func.min(BackupRecord.duration_seconds).label("min_duration_seconds"),
            func.max(BackupRecord.duration_seconds).label("max_duration_seconds"),
        )
        .where(
            BackupRecord.started_at >= recent_threshold,
            BackupRecord.status.in_(task_state_service.BACKUP_RECORD_TERMINAL_STATUSES),
            BackupRecord.duration_seconds.is_not(None),
        )
    ).one()
    avg_duration_seconds = round(float(duration_stats[0] or 0.0), 2)
    min_duration_seconds = round(float(duration_stats[1] or 0.0), 2)
    max_duration_seconds = round(float(duration_stats[2] or 0.0), 2)
    upload_rows = session.exec(
        select(
            TaskEvent.storage_type,
            func.count().label("attempt_count"),
            func.sum(case((TaskEvent.success.is_(True), 1), else_=0)).label("success_count"),
        )
        .where(
            TaskEvent.event == "backup_record_storage_upload",
            TaskEvent.created_at >= recent_threshold,
        )
        .group_by(TaskEvent.storage_type)
        .order_by(TaskEvent.storage_type)
    ).all()
    storage_uploads = [
        {
            "storage_type": storage_type or "UNKNOWN",
            "attempt_count": int(attempt_count or 0),
            "success_count": int(success_count or 0),
            "success_rate": round((int(success_count or 0) / int(attempt_count or 1)) * 100, 1) if int(attempt_count or 0) else 0.0,
        }
        for storage_type, attempt_count, success_count in upload_rows
    ]
    upload_attempt_total = sum(item["attempt_count"] for item in storage_uploads)
    upload_success_total = sum(item["success_count"] for item in storage_uploads)

    failure_rows = session.exec(
        select(
            BackupRecord.failure_type,
            func.count().label("count"),
        )
        .where(
            BackupRecord.started_at >= recent_threshold,
            BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_FAILED,
        )
        .group_by(BackupRecord.failure_type)
        .order_by(func.count().desc(), BackupRecord.failure_type)
        .limit(5)
    ).all()

    top_failure_types = [
        {
            "failure_type": failure_type or "UNKNOWN",
            "count": int(count or 0),
        }
        for failure_type, count in failure_rows
    ]

    platform_rows = session.exec(
        select(
            Device.platform,
            func.count().label("total"),
            func.sum(
                case((BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED, 1), else_=0)
            ).label("success_count"),
        )
        .join(Device, Device.id == BackupRecord.device_id)
        .where(
            BackupRecord.started_at >= recent_threshold,
            BackupRecord.status.in_(task_state_service.BACKUP_RECORD_TERMINAL_STATUSES),
        )
        .group_by(Device.platform)
        .order_by(func.count().desc(), Device.platform)
        .limit(5)
    ).all()
    platform_success_trends = [
        {
            "platform": platform or "UNKNOWN",
            "total": int(total or 0),
            "success_count": int(success_count or 0),
            "success_rate": round((int(success_count or 0) / int(total or 1)) * 100, 1) if int(total or 0) else 0.0,
        }
        for platform, total, success_count in platform_rows
    ]

    device_rows = session.exec(
        select(
            Device.name,
            Device.host,
            func.count().label("total"),
            func.sum(
                case((BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED, 1), else_=0)
            ).label("success_count"),
        )
        .join(Device, Device.id == BackupRecord.device_id)
        .where(
            BackupRecord.started_at >= recent_threshold,
            BackupRecord.status.in_(task_state_service.BACKUP_RECORD_TERMINAL_STATUSES),
        )
        .group_by(Device.name, Device.host)
        .order_by(
            (
                func.count()
                - func.sum(
                    case((BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED, 1), else_=0)
                )
            ).desc(),
            func.count().desc(),
            Device.name,
        )
        .limit(5)
    ).all()
    device_success_trends = [
        {
            "device_name": device_name or translate(locale, "task.health.unknown_device"),
            "device_host": device_host or "-",
            "total": int(total or 0),
            "success_count": int(success_count or 0),
            "fail_count": max(0, int(total or 0) - int(success_count or 0)),
            "success_rate": round((int(success_count or 0) / int(total or 1)) * 100, 1) if int(total or 0) else 0.0,
        }
        for device_name, device_host, total, success_count in device_rows
    ]
    success_rate = round(((recent_total - recent_failed) / recent_total) * 100, 1) if recent_total else 100.0

    return {
        "window_hours": normalized_window_hours,
        "recent_total": recent_total,
        "recent_failed": recent_failed,
        "recent_success_rate": success_rate,
        "running_count": running_count,
        "stale_running_count": stale_running_count,
        "active_schedule_runs": active_schedule_runs,
        "retry_scheduled_count": retry_scheduled_count,
        "avg_duration_seconds": avg_duration_seconds,
        "min_duration_seconds": min_duration_seconds,
        "max_duration_seconds": max_duration_seconds,
        "upload_attempt_total": upload_attempt_total,
        "upload_success_total": upload_success_total,
        "upload_success_rate": round((upload_success_total / upload_attempt_total) * 100, 1) if upload_attempt_total else 0.0,
        "storage_uploads": storage_uploads,
        "top_failure_types": top_failure_types,
        "platform_success_trends": platform_success_trends,
        "device_success_trends": device_success_trends,
        "degraded_components": task_runtime_config_service.get_task_runtime_degradation_status(locale),
    }


def _resolve_task_event_persistence_mode(event: str) -> str:
    if event in _TASK_EVENT_LOG_ONLY_EVENTS:
        return "log_only"
    if event in _TASK_EVENT_IMMEDIATE_PERSIST_EVENTS:
        return "immediate"
    return "buffered"


def _buffer_task_event(buffered_event: BufferedTaskEvent, *, logger: logging.Logger) -> None:
    global _LAST_TASK_EVENT_FLUSH_AT

    now = monotonic()
    overflow_count = 0

    with _TASK_EVENT_BUFFER_LOCK:
        _TASK_EVENT_BUFFER.append(buffered_event)
        if len(_TASK_EVENT_BUFFER) > _TASK_EVENT_BUFFER_MAX_SIZE:
            overflow_count = len(_TASK_EVENT_BUFFER) - _TASK_EVENT_BUFFER_MAX_SIZE
            del _TASK_EVENT_BUFFER[:overflow_count]

        should_flush = (
            len(_TASK_EVENT_BUFFER) >= _TASK_EVENT_BUFFER_BATCH_SIZE
            or (now - _LAST_TASK_EVENT_FLUSH_AT) >= _TASK_EVENT_BUFFER_FLUSH_INTERVAL_SECONDS
        )
        if not should_flush:
            pending_count = len(_TASK_EVENT_BUFFER)
        else:
            pending_count = 0
            events_to_flush = list(_TASK_EVENT_BUFFER)
            _TASK_EVENT_BUFFER.clear()
            _LAST_TASK_EVENT_FLUSH_AT = now

    if overflow_count:
        logger.warning(
            json.dumps(
                {
                    "status": "degraded",
                    "component": "task_event_buffer",
                    "reason": "buffer_overflow",
                    "dropped_count": overflow_count,
                },
                ensure_ascii=False,
            )
        )
    if pending_count:
        return

    _persist_task_events(
        events_to_flush,
        logger=logger,
        flush_reason="buffer_threshold",
        allow_requeue=True,
    )


def _persist_task_events(
    events: list[BufferedTaskEvent],
    *,
    logger: logging.Logger,
    flush_reason: str,
    allow_requeue: bool,
) -> None:
    if not events:
        return

    broadcast_payloads: list[dict[str, Any]] = []
    try:
        with session_scope() as session:
            for task_event in events:
                model = _to_task_event_model(task_event)
                session.add(model)
                session.flush()
                broadcast_payloads.append(task_event_bus_service.serialize_task_event_model(model))
    except SQLAlchemyError as exc:
        dropped_count = _requeue_task_events(events) if allow_requeue else len(events)
        logger.warning(
            json.dumps(
                {
                    "status": "degraded",
                    "component": "task_event_store",
                    "reason": str(exc),
                    "flush_reason": flush_reason,
                    "event_count": len(events),
                    "requeued": allow_requeue,
                    "dropped_count": dropped_count,
                },
                ensure_ascii=False,
            ),
            exc_info=True,
        )
        return

    if broadcast_payloads:
        task_event_bus_service.publish_task_events(broadcast_payloads)


def _requeue_task_events(events: list[BufferedTaskEvent]) -> int:
    with _TASK_EVENT_BUFFER_LOCK:
        _TASK_EVENT_BUFFER[:0] = events
        if len(_TASK_EVENT_BUFFER) <= _TASK_EVENT_BUFFER_MAX_SIZE:
            return 0

        dropped_count = len(_TASK_EVENT_BUFFER) - _TASK_EVENT_BUFFER_MAX_SIZE
        del _TASK_EVENT_BUFFER[:dropped_count]
        return dropped_count


def _to_task_event_model(task_event: BufferedTaskEvent) -> TaskEvent:
    details = json.dumps(task_event.extra, ensure_ascii=False) if task_event.extra else None
    return TaskEvent(
        event=task_event.event,
        task_id=_clean_string(task_event.task_id),
        record_id=_clean_string(task_event.record_id),
        run_id=_clean_string(task_event.run_id),
        request_id=_clean_string(task_event.request_id),
        device_id=_coerce_int(task_event.extra.get("device_id")),
        failure_type=_clean_string(task_event.failure_type),
        storage_type=_clean_string(task_event.extra.get("storage_type")),
        success=_coerce_bool(task_event.extra.get("success")),
        retries_done=_coerce_int(task_event.extra.get("retries_done")),
        max_retries=_coerce_int(task_event.extra.get("max_retries")),
        details=details,
    )


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


atexit.register(
    flush_task_event_buffer,
    logger=_SHUTDOWN_LOGGER,
    force=True,
    allow_requeue=False,
)
