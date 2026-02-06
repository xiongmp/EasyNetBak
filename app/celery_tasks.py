from __future__ import annotations

import json
import time
from datetime import datetime
from uuid import UUID

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app import crud
from app.celery_app import celery_app
from app.core.settings import settings
from app.db import session_scope
from app.platforms import platforms_compatible
from app.services.alert_service import check_and_alert, check_and_alert_batch
from app.services.backup_service import backup_device
from app.services.netmiko_client import NetmikoClientError
from app.services.s3_service import upload_backup_to_s3


def celery_enabled() -> bool:
    return bool((settings.celery.broker_url or "").strip())


def _parse_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _retry_countdown(retry_index: int) -> int:
    base = max(1, int(settings.celery.backup_retry_backoff_seconds or 10))
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
        record = crud.get_backup(session, rid)
        if record is None:
            return {"ok": False, "reason": "record_not_found", "record_id": str(rid)}
        if record.finished_at is not None:
            return {"ok": True, "reason": "already_finished", "record_id": str(rid)}

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
                check_and_alert(session, record2, skip_email=skip_email)
        return {"ok": True, "record_id": str(rid)}

    except Exception as exc:
        duration = time.time() - start_time
        if isinstance(exc, SoftTimeLimitExceeded):
            failure_type = "TIME_LIMIT"
        else:
            failure_type = exc.failure_type if isinstance(exc, NetmikoClientError) else "UNKNOWN"

        retries_done = int(getattr(self.request, "retries", 0) or 0)
        max_retries = int(settings.celery.backup_max_retries or 0)

        if retries_done < max_retries and _is_retryable_failure(exc, failure_type):
            raise self.retry(
                exc=exc,
                countdown=_retry_countdown(retries_done),
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
    try:
        backup_record_task.apply_async(
            args=[str(record_id), int(device_id), int(template_id) if template_id is not None else None],
            kwargs={"skip_email": bool(skip_email)},
            task_id=str(record_id),
        )
        return True
    except Exception:
        return False


def enqueue_schedule_run(*, run_id: UUID, jobs: list[tuple[int, UUID, int | None]]) -> bool:
    if not celery_enabled():
        return False
    try:
        backup_ids: list[str] = []
        for did, bid, tpl_id in jobs:
            backup_ids.append(str(bid))
            backup_record_task.apply_async(
                args=[str(bid), int(did), int(tpl_id) if tpl_id is not None else None],
                kwargs={"skip_email": True},
                task_id=str(bid),
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
