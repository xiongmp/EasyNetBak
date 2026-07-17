from __future__ import annotations

import json
import time
import contextlib
import redis
import logging
from uuid import UUID

from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import task_failure, task_revoked
from sqlmodel import select

from app import crud
from app.celery_app import celery_app
from app.core.settings import settings
from app.db import session_scope
from app.models import BackupScheduleRunItem
from app.services.netmiko_client import NetmikoClientError
from app.services.reachability import perform_single_reachability_check
from app.services import task_execution_service, task_observability_service, task_orchestration_service, task_runtime_config_service, task_state_service
from app.core.logger import get_request_id
from typing import Any

logger = logging.getLogger(__name__)

_SEMAPHORE_TTL_SECONDS = 3600
_SEMAPHORE_RETRY_LOG_INTERVAL = 12
_SEMAPHORE_MAX_RETRIES = 24
_SEMAPHORE_FULL_MAX_RETRIES = 1_000_000
_SEMAPHORE_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local task_id = ARGV[1]
local max_slots = tonumber(ARGV[2])
local ttl_seconds = tonumber(ARGV[3])

if redis.call("SISMEMBER", key, task_id) == 1 then
    redis.call("EXPIRE", key, ttl_seconds)
    return 1
end

if redis.call("SCARD", key) < max_slots then
    redis.call("SADD", key, task_id)
    redis.call("EXPIRE", key, ttl_seconds)
    return 1
end

return 0
"""


def celery_enabled() -> bool:
    return bool((settings.celery.broker_url or "").strip())


def _log_degraded_component(
    *,
    component: str,
    reason: str,
    **extra: Any,
) -> None:
    logger.warning(
        json.dumps(
            {
                "status": "degraded",
                "component": component,
                "reason": reason,
                **extra,
            },
            ensure_ascii=False,
        )
    )


def _get_redis_client() -> redis.Redis | None:
    url = (settings.celery.broker_url or "").strip()
    if not url:
        return None
    try:
        return redis.from_url(url)
    except (redis.RedisError, ValueError, TypeError) as exc:
        _log_degraded_component(
            component="redis",
            reason=str(exc),
            broker_url=url,
        )
        return None


@contextlib.contextmanager
def task_semaphore(
    redis_client: redis.Redis,
    semaphore_key: str,
    max_slots: int,
    task_id: str,
    *,
    fail_open: bool = False,
):
    """Redis-backed global task semaphore."""
    if max_slots <= 0:
        yield True, ""
        return

    added = False
    try:
        try:
            acquired = redis_client.eval(
                _SEMAPHORE_ACQUIRE_SCRIPT,
                1,
                semaphore_key,
                task_id,
                int(max_slots),
                _SEMAPHORE_TTL_SECONDS,
            )
            if int(acquired) == 1:
                added = True
                yield True, ""
            else:
                yield False, "redis_semaphore_full"
        except redis.RedisError as exc:
            _log_degraded_component(
                component="redis_semaphore",
                reason=str(exc),
                semaphore_key=semaphore_key,
                task_id=task_id,
                max_slots=max_slots,
                fail_open=fail_open,
            )
            yield bool(fail_open), "redis_semaphore_error"
    finally:
        if added:
            try:
                redis_client.srem(semaphore_key, task_id)
            except redis.RedisError as exc:
                _log_degraded_component(
                    component="redis_semaphore_release",
                    reason=str(exc),
                    semaphore_key=semaphore_key,
                    task_id=task_id,
                )


def _parse_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _should_log_semaphore_retry(retries_done: int) -> bool:
    retry_count = max(0, int(retries_done or 0))
    return retry_count == 0 or retry_count % _SEMAPHORE_RETRY_LOG_INTERVAL == 0


def _log_redis_semaphore_degraded_event(
    *,
    task_id: str,
    record_id: UUID,
    run_id: UUID | None,
    request_id: str,
    device_id: int,
    reason: str,
    failure_type: str,
    max_slots: int,
    fail_open: bool,
    retries_done: int,
    max_retries: int,
) -> None:
    event_record_id = "" if run_id else str(record_id)
    task_observability_service.log_task_event(
        logger,
        level="warning",
        event="backup_record_semaphore_degraded",
        task_id=task_id,
        record_id=event_record_id,
        run_id=str(run_id or ""),
        request_id=request_id,
        failure_type=failure_type,
        component="redis_semaphore",
        reason=reason,
        source_record_id=str(record_id),
        device_id=device_id,
        max_slots=max_slots,
        fail_open=fail_open,
        retries_done=retries_done,
        max_retries=max_retries,
        retry_countdown=5,
    )


def _retry_countdown(retry_index: int, backoff_base: int = 10) -> int:
    base = max(1, int(backoff_base or 10))
    retry_index = max(0, int(retry_index or 0))
    countdown = base * (2**retry_index)
    return int(min(300, max(base, countdown)))


def _format_duration_text(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return ""
    seconds = max(0, int(duration_seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h {minute}m {sec}s"


def _is_retryable_failure(exc: Exception, failure_type: str) -> bool:
    if isinstance(exc, SoftTimeLimitExceeded):
        return True
    if isinstance(exc, NetmikoClientError):
        return failure_type in {
            "TIMEOUT",
            "READ_TIMEOUT",
            "DISCONNECTED",
            "SESSION_LIMIT",
            "NETWORK_UNREACHABLE",
            "REFUSED",
        }
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _mark_backup_record_failed_if_unfinished(
    record_id: UUID | str,
    *,
    error_message: str,
    failure_type: str,
) -> bool:
    try:
        rid = _parse_uuid(record_id)
    except (TypeError, ValueError):
        return False
    with session_scope() as session:
        record = crud.get_backup(session, rid)
        if record is None or task_state_service.is_backup_record_terminal_status(record.status):
            return False
        crud.finish_backup_record(
            session,
            record_id=rid,
            success=False,
            config_text=None,
            error_message=(error_message or "").strip() or "Task failed",
            duration_seconds=None,
            failure_type=(failure_type or "").strip() or "UNKNOWN",
        )
    return True


class BaseTask(Task):
    acks_late = True
    reject_on_worker_lost = True


@celery_app.task(bind=True, base=BaseTask, name="app.backup_record")
def backup_record_task(
    self: BaseTask,
    record_id: UUID | str,
    device_id: int,
    template_id: int | None,
    *,
    skip_email: bool = False,
    failure_retries_done: int = 0,
) -> dict[str, object]:
    rid = _parse_uuid(record_id)
    did = int(device_id)
    tpl_id = int(template_id) if template_id is not None else None

    runtime_config = task_runtime_config_service.load_backup_task_runtime_config()
    run_id: UUID | None = None
    with session_scope() as session:
        record = crud.get_backup(session, rid)
        if record is None:
            return {"ok": False, "reason": "record_not_found", "record_id": str(rid)}
        if task_state_service.is_backup_record_terminal_status(record.status):
            return {"ok": True, "reason": "already_finished", "record_id": str(rid)}
        run_id = session.exec(
            select(BackupScheduleRunItem.run_id)
            .where(BackupScheduleRunItem.backup_id == rid)
            .limit(1)
        ).first()

    # Global concurrency control.
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    request_id = get_request_id() or ""
    retries_done = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
    redis_client = _get_redis_client()
    semaphore_fail_open = bool(settings.celery.redis_semaphore_fail_open)
    if redis_client:
        with task_semaphore(
            redis_client,
            "semaphore:backup",
            runtime_config.max_slots,
            str(rid),
            fail_open=semaphore_fail_open,
        ) as (acquired, semaphore_reason):
            if not acquired:
                retry_max_retries = (
                    _SEMAPHORE_FULL_MAX_RETRIES
                    if semaphore_reason == "redis_semaphore_full"
                    else _SEMAPHORE_MAX_RETRIES
                )
                should_log_semaphore_wait = semaphore_reason != "redis_semaphore_full"
                if should_log_semaphore_wait and _should_log_semaphore_retry(retries_done):
                    _log_redis_semaphore_degraded_event(
                        task_id=task_id,
                        record_id=rid,
                        run_id=run_id,
                        request_id=request_id,
                        device_id=did,
                        reason=semaphore_reason or "redis_semaphore_unavailable",
                        failure_type=(
                            "REDIS_SEMAPHORE_FULL"
                            if semaphore_reason == "redis_semaphore_full"
                            else "REDIS_SEMAPHORE_UNAVAILABLE"
                        ),
                        max_slots=runtime_config.max_slots,
                        fail_open=semaphore_fail_open,
                        retries_done=retries_done,
                        max_retries=retry_max_retries,
                    )
                    task_observability_service.flush_task_event_buffer(logger=logger, force=True)
                # Retry shortly when the global backup semaphore is full.
                raise self.retry(countdown=5, max_retries=retry_max_retries)
            
            # Continue once a semaphore slot is acquired.
            return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                       failure_retries_done=failure_retries_done,
                                       max_retries=runtime_config.max_retries, backoff_base=runtime_config.backoff_base)
    if semaphore_fail_open:
        # Explicit emergency override: continue without global concurrency control.
        return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                   failure_retries_done=failure_retries_done,
                                   max_retries=runtime_config.max_retries, backoff_base=runtime_config.backoff_base)
    _log_degraded_component(
        component="redis_semaphore",
        reason="redis_client_unavailable",
        semaphore_key="semaphore:backup",
        task_id=str(rid),
        max_slots=runtime_config.max_slots,
        fail_open=semaphore_fail_open,
    )
    if _should_log_semaphore_retry(retries_done):
        _log_redis_semaphore_degraded_event(
            task_id=task_id,
            record_id=rid,
            run_id=run_id,
            request_id=request_id,
            device_id=did,
            reason="redis_client_unavailable",
            failure_type="REDIS_SEMAPHORE_UNAVAILABLE",
            max_slots=runtime_config.max_slots,
            fail_open=semaphore_fail_open,
            retries_done=retries_done,
            max_retries=_SEMAPHORE_MAX_RETRIES,
        )
        task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    raise self.retry(countdown=5, max_retries=_SEMAPHORE_MAX_RETRIES)


def _execute_backup_logic(
    self: BaseTask,
    rid: UUID,
    did: int,
    tpl_id: int | None,
    *,
    skip_email: bool = False,
    failure_retries_done: int = 0,
    max_retries: int = 3,
    backoff_base: int = 10,
) -> dict[str, object]:
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    request_id = get_request_id() or ""
    with session_scope() as session:
        try:
            context = task_execution_service.load_backup_execution_context(
                session,
                record_id=rid,
                device_id=did,
                template_id=tpl_id,
                task_id=task_id,
                request_id=request_id,
                skip_email=skip_email,
            )
        except task_execution_service.BackupExecutionAborted as exc:
            return exc.response

    retries_done = max(0, int(failure_retries_done or 0))
    while True:
        task_observability_service.log_task_event(
            logger,
            level="info",
            event="backup_record_task_started",
            task_id=task_id,
            record_id=str(rid),
            request_id=request_id,
            device_id=did,
            template_id=tpl_id,
        )
        start_time = time.time()
        try:
            run_result = task_execution_service.run_backup_execution(context)
            duration = time.time() - start_time
            finalize_result = task_execution_service.finalize_backup_execution(
                context=context,
                skip_email=skip_email,
                duration=duration,
                result=run_result,
            )
            return finalize_result.response

        except Exception as exc:
            duration = time.time() - start_time
            finalize_result = task_execution_service.finalize_backup_execution(
                context=context,
                skip_email=skip_email,
                duration=duration,
                error=exc,
                retries_done=retries_done,
                max_retries=max_retries,
                backoff_base=backoff_base,
                is_retryable_failure=_is_retryable_failure,
                build_retry_countdown=_retry_countdown,
            )
            if finalize_result.should_retry:
                retries_done += 1
                countdown = int(finalize_result.retry_countdown or 0)
                if countdown > 0:
                    time.sleep(countdown)
                continue
            return finalize_result.response


@celery_app.task(bind=True, base=BaseTask, name="app.finalize_schedule_run")
def finalize_schedule_run_task(
    self: BaseTask,
    run_id: UUID | str,
    backup_ids: list[UUID | str],
) -> dict[str, object]:
    rid = _parse_uuid(run_id)
    bids = [_parse_uuid(x) for x in (backup_ids or [])]
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    request_id = get_request_id() or ""
    retry_count = int(getattr(self.request, "retries", 0) or 0)
    if retry_count == 0:
        task_observability_service.log_task_event(
            logger,
            level="info",
            event="finalize_schedule_run_started",
            task_id=task_id,
            run_id=str(rid),
            request_id=request_id,
            backup_count=len(bids),
            retries_done=retry_count,
        )

    with session_scope() as session:
        decision = task_orchestration_service.finalize_schedule_run(
            session,
            run_id=rid,
            backup_ids=bids,
            retries_done=retry_count,
        )
        if decision.should_retry:
            raise self.retry(
                countdown=decision.retry_countdown,
                max_retries=int(settings.celery.schedule_finalize_max_polls or 0),
            )

        response = decision.response
        reason = str(response.get("reason", "") or "")
        if reason == "run_not_found":
            task_observability_service.log_task_event(
                logger,
                level="warning",
                event="finalize_schedule_run_missing",
                task_id=task_id,
                run_id=str(rid),
                request_id=request_id,
            )
            task_observability_service.flush_task_event_buffer(logger=logger, force=True)
            return response
        if reason == "already_finished":
            task_observability_service.log_task_event(
                logger,
                level="info",
                event="finalize_schedule_run_skipped",
                task_id=task_id,
                run_id=str(rid),
                request_id=request_id,
                reason="already_finished",
            )
            task_observability_service.flush_task_event_buffer(logger=logger, force=True)
            return response

        completed_run = crud.get_schedule_run(session, rid)
        duration_seconds: int | None = None
        if completed_run and completed_run.started_at and completed_run.finished_at:
            duration_seconds = max(
                0,
                int((completed_run.finished_at - completed_run.started_at).total_seconds()),
            )
        task_observability_service.log_task_event(
            logger,
            level="info",
            event="finalize_schedule_run_completed",
            task_id=task_id,
            run_id=str(rid),
            request_id=request_id,
            backup_count=len(bids),
            success_count=decision.success_count,
            fail_count=decision.fail_count,
            duration_seconds=duration_seconds,
            duration_text=_format_duration_text(duration_seconds),
        )
        task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    return response
@celery_app.task(bind=True, base=BaseTask, name="app.bulk_reachability")
def bulk_reachability_task(
    self: BaseTask,
    device_ids: list[int],
    offset_minutes: int = 0,
) -> dict[str, Any]:
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    request_id = get_request_id() or ""
    total = len(device_ids)
    processed = 0
    success = 0
    failed = 0
    items: list[dict[str, Any]] = []
    runtime_config = task_runtime_config_service.load_backup_task_runtime_config()

    # Initialize state
    self.update_state(state="PROGRESS", meta={
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "items": items,
    })

    max_workers = max(1, int(runtime_config.max_slots or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(perform_single_reachability_check, did, offset_minutes=offset_minutes): did for did in device_ids}
        
        for future in as_completed(futures):
            did = int(futures.get(future, 0) or 0)
            try:
                result = future.result()
                if result:
                    items.append(result)
                    if result["success"]:
                        success += 1
                    else:
                        failed += 1
            except Exception as exc:
                failed += 1
                items.append(
                    {
                        "id": did,
                        "name": "",
                        "host": "",
                        "success": False,
                        "error_message": str(exc),
                        "duration_ms": 0,
                        "last_checked": "",
                        "login_method": "",
                    }
                )
                task_observability_service.log_task_event(
                    logger,
                    level="exception",
                    event="bulk_reachability_device_error",
                    task_id=task_id,
                    request_id=request_id,
                    device_id=did or None,
                    error=str(exc),
                )
            
            processed += 1
            self.update_state(state="PROGRESS", meta={
                "total": total,
                "processed": processed,
                "success": success,
                "failed": failed,
                "items": items,
            })

    return {
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "items": items,
    }


@celery_app.task(bind=True, base=BaseTask, name="app.deliver_notification")
def deliver_notification_task(self, delivery_id: int):
    """Retry one persisted notification delivery without affecting backup state."""
    from app.models import NotificationDelivery
    from app.services import notification_routing_service

    with session_scope() as session:
        delivery = session.get(NotificationDelivery, int(delivery_id))
        if delivery is None:
            if self.request.retries < 3:
                raise self.retry(countdown=5)
            return {"ok": False, "reason": "delivery_missing"}
        success = notification_routing_service.retry_delivery(session, int(delivery_id))
        return {"ok": bool(success), "delivery_id": int(delivery_id), "status": delivery.status}


@task_failure.connect(sender=backup_record_task)
def _on_backup_record_task_failure(
    sender=None,
    task_id: str | None = None,
    exception: Exception | None = None,
    args=None,
    **kwargs,
):
    message = str(exception) if exception else "Task failed"
    record_id = str((args or [""])[0] or task_id or "")
    failure_type = "TASK_FAILURE"
    if isinstance(exception, SoftTimeLimitExceeded):
        failure_type = "TIME_LIMIT"
    task_observability_service.log_task_event(
        logger,
        level="warning",
        event="backup_record_task_signal_failure",
        task_id=task_id or "",
        record_id=record_id,
        request_id=get_request_id() or "",
        failure_type=failure_type,
        error=message,
    )
    task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    _mark_backup_record_failed_if_unfinished(
        record_id,
        error_message=message,
        failure_type=failure_type,
    )


@task_revoked.connect(sender=backup_record_task)
def _on_backup_record_task_revoked(
    sender=None,
    request=None,
    terminated: bool = False,
    signum=None,
    expired: bool = False,
    **kwargs,
):
    task_id = str(getattr(request, "id", "") or "")
    reason = "Task revoked"
    if expired:
        reason = "Task expired"
    elif terminated:
        reason = f"Task terminated (signal={signum})"
    task_observability_service.log_task_event(
        logger,
        level="warning",
        event="backup_record_task_revoked",
        task_id=task_id,
        record_id=task_id,
        request_id=get_request_id() or "",
        failure_type="TASK_REVOKED",
        error=reason,
    )
    task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    try:
        rid = _parse_uuid(task_id)
    except (TypeError, ValueError):
        return
    with session_scope() as session:
        record = crud.get_backup(session, rid)
        if record is None or task_state_service.is_backup_record_terminal_status(record.status):
            return
        crud.cancel_backup_record(
            session,
            record_id=rid,
            error_message=reason,
            failure_type="TASK_REVOKED",
        )
