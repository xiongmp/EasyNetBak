from __future__ import annotations

import json
import time
import contextlib
import redis
import logging
from datetime import datetime
from uuid import UUID

from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app import crud
from app.celery_app import celery_app
from app.core.settings import settings
from app.db import session_scope
from app.platforms import platforms_compatible
from app.services.alert_service import check_and_alert, check_and_alert_batch
from app.services.backup_service import backup_device
from app.services.ftp_service import upload_backup_to_ftp
from app.services.netmiko_client import NetmikoClientError
from app.services.reachability import perform_single_reachability_check
from app.services.s3_service import upload_backup_to_s3
from app.core.logger import get_request_id
from typing import Any

logger = logging.getLogger(__name__)


def celery_enabled() -> bool:
    return bool((settings.celery.broker_url or "").strip())


def _get_redis_client() -> redis.Redis | None:
    url = (settings.celery.broker_url or "").strip()
    if not url:
        return None
    try:
        return redis.from_url(url)
    except Exception:
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
    finally:
        if added:
            redis_client.srem(semaphore_key, task_id)


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

    with session_scope() as session:
        # 获取并发限制及重试/超时等设置
        try:
            val_concurrent = crud.get_setting(session, key="max_concurrent_tasks")
            max_slots = int(val_concurrent or "10")
            
            val_retries = crud.get_setting(session, key="backup_max_retries")
            max_retries = int(val_retries or str(settings.celery.backup_max_retries))
            
            val_backoff = crud.get_setting(session, key="backup_retry_backoff")
            backoff_base = int(val_backoff or str(settings.celery.backup_retry_backoff_seconds))

            val_timeout = crud.get_setting(session, key="task_time_limit")
            time_limit = int(val_timeout if (val_timeout and val_timeout != "0") else str(settings.celery.task_time_limit_seconds))
        except Exception:
            max_slots = 10
            max_retries = int(settings.celery.backup_max_retries)
            backoff_base = int(settings.celery.backup_retry_backoff_seconds)
            time_limit = int(settings.celery.task_time_limit_seconds)

        record = crud.get_backup(session, rid)
        if record is None:
            return {"ok": False, "reason": "record_not_found", "record_id": str(rid)}
        if record.finished_at is not None:
            return {"ok": True, "reason": "already_finished", "record_id": str(rid)}

    # 并发控制
    redis_client = _get_redis_client()
    if redis_client:
        with task_semaphore(redis_client, "semaphore:backup", max_slots, str(rid)) as acquired:
            if not acquired:
                # 如果没有获得槽位，则重试（5-10秒后）
                raise self.retry(countdown=5, max_retries=100)
            
            # 获得槽位后继续执行备份逻辑
            return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                       max_retries=max_retries, backoff_base=backoff_base)
    else:
        # 如果 Redis 不可用，则降级为无并发控制
        return _execute_backup_logic(self, rid, did, tpl_id, skip_email=skip_email, 
                                   max_retries=max_retries, backoff_base=backoff_base)


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
    with session_scope() as session:
        record = crud.get_backup(session, rid)
        device = crud.get_device(session, did)
        if device is None:
            record2 = crud.finish_backup_record(
                session,
                record_id=rid,
                success=False,
                config_text=None,
                error_message="Device not found",
                failure_type="DEVICE_NOT_FOUND",
            )
            if record2:
                check_and_alert(session, record2, skip_email=skip_email)
            return {"ok": False, "reason": "device_not_found", "record_id": str(rid)}

        device_name = str(device.name or "")
        device_host = str(device.host or "")
        device_port = int(getattr(device, "port", 22) or 22)
        device_login_method = str(getattr(device, "login_method", "ssh") or "ssh")
        device_platform = str(getattr(device, "platform", "") or "")

        effective_tpl_id = tpl_id or getattr(device, "default_template_id", None)
        template = crud.get_template(session, effective_tpl_id) if effective_tpl_id else None
        if effective_tpl_id and template is None:
            record2 = crud.finish_backup_record(
                session,
                record_id=rid,
                success=False,
                config_text=None,
                error_message="Template not found",
                failure_type="TEMPLATE_NOT_FOUND",
            )
            if record2:
                check_and_alert(session, record2, skip_email=skip_email)
            return {"ok": False, "reason": "template_not_found", "record_id": str(rid)}

        if template is not None and not platforms_compatible(template.platform, device.platform):
            record2 = crud.finish_backup_record(
                session,
                record_id=rid,
                success=False,
                config_text=None,
                error_message="Template platform mismatch",
                failure_type="PLATFORM_MISMATCH",
            )
            if record2:
                check_and_alert(session, record2, skip_email=skip_email)
            return {"ok": False, "reason": "platform_mismatch", "record_id": str(rid)}

        secrets = crud.get_device_secrets(session, device)
        template_commands = str(template.commands) if template and getattr(template, "commands", None) is not None else None

    start_time = time.time()
    try:
        if not secrets.get("username"):
            raise RuntimeError("Missing credential for device")

        config_text = backup_device(
            host=device_host,
            port=device_port,
            login_method=device_login_method,
            platform=device_platform,
            username=secrets["username"],
            password=secrets["password"],
            enable_password=secrets["enable_password"],
            template_commands=template_commands,
        )
        duration = time.time() - start_time

        with session_scope() as session:
            record2 = crud.finish_backup_record(
                session,
                record_id=rid,
                success=True,
                config_text=config_text,
                error_message=None,
                duration_seconds=duration,
                failure_type=None,
            )
            if record2:
                upload_backup_to_s3(
                    session=session,
                    device_name=device_name,
                    host=device_host,
                    config_text=config_text,
                    finished_at=record2.finished_at or record2.started_at,
                )
                upload_backup_to_ftp(
                    session=session,
                    device_name=device_name,
                    host=device_host,
                    config_text=config_text,
                    finished_at=record2.finished_at or record2.started_at,
                )
                check_and_alert(session, record2, skip_email=skip_email)
        return {"ok": True, "record_id": str(rid)}

    except Exception as exc:
        duration = time.time() - start_time
        if isinstance(exc, SoftTimeLimitExceeded):
            failure_type = "TIME_LIMIT"
        else:
            failure_type = getattr(exc, "failure_type", "UNKNOWN") if isinstance(exc, NetmikoClientError) else "UNKNOWN"

        retries_done = int(getattr(self.request, "retries", 0) or 0)

        if retries_done < max_retries and _is_retryable_failure(exc, failure_type):
            raise self.retry(
                exc=exc,
                countdown=_retry_countdown(retries_done, backoff_base=backoff_base),
                max_retries=max_retries,
            )

        with session_scope() as session:
            record2 = crud.finish_backup_record(
                session,
                record_id=rid,
                success=False,
                config_text=None,
                error_message=str(exc),
                duration_seconds=duration,
                failure_type=failure_type,
            )
            if record2:
                check_and_alert(session, record2, skip_email=skip_email)

        return {"ok": False, "record_id": str(rid), "error": str(exc), "failure_type": failure_type}


@celery_app.task(bind=True, base=BaseTask, name="app.finalize_schedule_run")
def finalize_schedule_run_task(
    self: BaseTask,
    run_id: UUID | str,
    backup_ids: list[UUID | str],
) -> dict[str, object]:
    rid = _parse_uuid(run_id)
    bids = [_parse_uuid(x) for x in (backup_ids or [])]

    if not bids:
        with session_scope() as session:
            crud.finish_schedule_run(session, run_id=rid, success_count=0, fail_count=0, error_message=None)
        return {"ok": True, "run_id": str(rid), "reason": "no_jobs"}

    with session_scope() as session:
        run = crud.get_schedule_run(session, rid)
        if run is None:
            return {"ok": False, "run_id": str(rid), "reason": "run_not_found"}
        if run.finished_at is not None:
            return {"ok": True, "run_id": str(rid), "reason": "already_finished"}

        records = crud.list_backups_by_ids(session, bids)
        finished = [r for r in records if r.finished_at is not None]
        if len(finished) < len(bids):
            retries_done = int(getattr(self.request, "retries", 0) or 0)
            if retries_done < int(settings.celery.schedule_finalize_max_polls or 0):
                poll = max(1, int(settings.celery.schedule_finalize_poll_seconds or 5))
                raise self.retry(
                    countdown=poll,
                    max_retries=int(settings.celery.schedule_finalize_max_polls or 0),
                )

        success_count = 0
        fail_count = 0
        failures_by_type: dict[str, int] = {}
        for r in finished:
            if r.success:
                success_count += 1
            else:
                fail_count += 1
                ft = str(r.failure_type or "UNKNOWN")
                failures_by_type[ft] = failures_by_type.get(ft, 0) + 1

        error_payload: dict[str, object] = {}
        if len(finished) < len(bids):
            error_payload["unfinished_backups"] = int(len(bids) - len(finished))
        if failures_by_type:
            error_payload["failures_by_type"] = failures_by_type

        error_message = json.dumps(error_payload, ensure_ascii=False) if error_payload else None
        crud.finish_schedule_run(
            session,
            run_id=rid,
            success_count=success_count,
            fail_count=fail_count,
            error_message=error_message,
        )
        check_and_alert_batch(session, rid)

        retention_days_str = crud.get_setting(session, key="backup_retention_days")
        try:
            days = int(retention_days_str or "90")
        except Exception:
            days = 90
        if days > 0:
            crud.cleanup_old_backups(session, days)

    return {
        "ok": True,
        "run_id": str(rid),
        "success": int(success_count),
        "fail": int(fail_count),
        "finished_at": datetime.utcnow().isoformat(),
    }


def enqueue_backup_record(
    *,
    record_id: UUID,
    device_id: int,
    template_id: int | None,
    skip_email: bool,
) -> bool:
    if not celery_enabled():
        return False
    
    # 获取任务超时设置
    time_limit = int(settings.celery.task_time_limit_seconds or 300)
    with session_scope() as session:
        try:
            val_timeout = crud.get_setting(session, key="task_time_limit")
            if val_timeout and val_timeout != "0":
                time_limit = int(val_timeout)
        except:
            pass

    try:
        backup_record_task.apply_async(
            args=[str(record_id), int(device_id), int(template_id) if template_id is not None else None],
            kwargs={"skip_email": bool(skip_email)},
            task_id=str(record_id),
            time_limit=time_limit if time_limit > 0 else None,
            soft_time_limit=max(1, time_limit - 10) if time_limit > 10 else None,
        )
        return True
    except Exception:
        return False


def enqueue_schedule_run(*, run_id: UUID, jobs: list[tuple[int, UUID, int | None]]) -> bool:
    if not celery_enabled():
        return False
    
    # 获取任务超时设置
    time_limit = int(settings.celery.task_time_limit_seconds or 300)
    with session_scope() as session:
        try:
            val_timeout = crud.get_setting(session, key="task_time_limit")
            if val_timeout and val_timeout != "0":
                time_limit = int(val_timeout)
        except:
            pass

    try:
        backup_ids: list[str] = []
        for did, bid, tpl_id in jobs:
            backup_ids.append(str(bid))
            backup_record_task.apply_async(
                args=[str(bid), int(did), int(tpl_id) if tpl_id is not None else None],
                kwargs={"skip_email": True},
                task_id=str(bid),
                time_limit=time_limit if time_limit > 0 else None,
                soft_time_limit=max(1, time_limit - 10) if time_limit > 10 else None,
            )
        poll = max(1, int(settings.celery.schedule_finalize_poll_seconds or 5))
        finalize_schedule_run_task.apply_async(
            args=[str(run_id), backup_ids],
            countdown=poll,
            task_id=f"finalize-{str(run_id)}",
        )
        return True
    except Exception:
        return False


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

    # Initialize state
    self.update_state(state="PROGRESS", meta={
        "total": total,
        "processed": processed,
        "success": success,
        "failed": failed,
        "items": items,
    })

    max_workers = 10
    with session_scope() as session:
        try:
            val = crud.get_setting(session, key="max_concurrent_tasks")
            max_workers = int(val or "10")
        except Exception as exc:
            logger.exception(
                json.dumps(
                    {
                        "event": "bulk_reachability_config_error",
                        "task_id": task_id,
                        "request_id": request_id,
                        "device_id": None,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )

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
                logger.exception(
                    json.dumps(
                        {
                            "event": "bulk_reachability_device_error",
                            "task_id": task_id,
                            "request_id": request_id,
                            "device_id": did or None,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
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
