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

from app import crud
from app.celery_app import celery_app
from app.core.settings import settings
from app.db import session_scope
from app.services.netmiko_client import NetmikoClientError
from app.services.reachability import perform_single_reachability_check
from app.services import task_execution_service, task_observability_service, task_orchestration_service, task_runtime_config_service, task_state_service
from app.core.logger import get_request_id
from typing import Any

logger = logging.getLogger(__name__)


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
def task_semaphore(redis_client: redis.Redis, semaphore_key: str, max_slots: int, task_id: str):
    """基于 Redis 的任务信号量，控制全局并发数"""
    if max_slots <= 0:
        yield True
        return

    # 尝试将当前任务加入集合
    # 注意：这里使用集合来确保幂等性（同一个任务多次进入只算一个）
    added = False
    try:
        try:
            # 检查当前运行数
            current_count = redis_client.scard(semaphore_key)
            if current_count < max_slots:
                redis_client.sadd(semaphore_key, task_id)
                # 设置过期时间防止死锁（例如 1 小时）
                redis_client.expire(semaphore_key, 3600)
                added = True
                yield True
            else:
                yield False
        except redis.RedisError as exc:
            _log_degraded_component(
                component="redis_semaphore",
                reason=str(exc),
                semaphore_key=semaphore_key,
                task_id=task_id,
                max_slots=max_slots,
            )
            # Redis 信号量不可用时降级为无并发控制，避免任务完全阻塞。
            yield True
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


def _retry_countdown(retry_index: int, backoff_base: int = 10) -> int:
    base = max(1, int(backoff_base or 10))
    retry_index = max(0, int(retry_index or 0))
    countdown = base * (2**retry_index)
    return int(min(300, max(base, countdown)))


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
) -> dict[str, object]:
    rid = _parse_uuid(record_id)
    did = int(device_id)
    tpl_id = int(template_id) if template_id is not None else None

    runtime_config = task_runtime_config_service.load_backup_task_runtime_config()
    with session_scope() as session:
        record = crud.get_backup(session, rid)
        if record is None:
            return {"ok": False, "reason": "record_not_found", "record_id": str(rid)}
        if task_state_service.is_backup_record_terminal_status(record.status):
            return {"ok": True, "reason": "already_finished", "record_id": str(rid)}

    # 并发控制
    redis_client = _get_redis_client()
    if redis_client:
        with task_semaphore(redis_client, "semaphore:backup", runtime_config.max_slots, str(rid)) as acquired:
            if not acquired:
                # 如果没有获得槽位，则重试（5-10秒后）
                raise self.retry(countdown=5, max_retries=100)
            
            # 获得槽位后继续执行备份逻辑
            return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                       max_retries=runtime_config.max_retries, backoff_base=runtime_config.backoff_base)
    else:
        # 如果 Redis 不可用，则降级为无并发控制
        return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                   max_retries=runtime_config.max_retries, backoff_base=runtime_config.backoff_base)


def _execute_backup_logic(
    self: BaseTask,
    rid: UUID,
    did: int,
    tpl_id: int | None,
    *,
    skip_email: bool = False,
    max_retries: int = 3,
    backoff_base: int = 10,
) -> dict[str, object]:
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    request_id = get_request_id() or ""
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
        retries_done = int(getattr(self.request, "retries", 0) or 0)
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
            raise self.retry(
                exc=exc,
                countdown=finalize_result.retry_countdown,
                max_retries=max_retries,
            )
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
    task_observability_service.log_task_event(
        logger,
        level="info",
        event="finalize_schedule_run_started",
        task_id=task_id,
        run_id=str(rid),
        request_id=request_id,
        backup_count=len(bids),
    )

    with session_scope() as session:
        decision = task_orchestration_service.finalize_schedule_run(
            session,
            run_id=rid,
            backup_ids=bids,
            retries_done=int(getattr(self.request, "retries", 0) or 0),
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


@task_failure.connect(sender=backup_record_task)
def _on_backup_record_task_failure(
    sender=None,
    task_id: str | None = None,
    exception: Exception | None = None,
    **kwargs,
):
    message = str(exception) if exception else "Task failed"
    failure_type = "TASK_FAILURE"
    if isinstance(exception, SoftTimeLimitExceeded):
        failure_type = "TIME_LIMIT"
    task_observability_service.log_task_event(
        logger,
        level="warning",
        event="backup_record_task_signal_failure",
        task_id=task_id or "",
        record_id=task_id or "",
        request_id=get_request_id() or "",
        failure_type=failure_type,
        error=message,
    )
    task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    _mark_backup_record_failed_if_unfinished(
        task_id or "",
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
    _mark_backup_record_failed_if_unfinished(
        task_id,
        error_message=reason,
        failure_type="TASK_REVOKED",
    )
