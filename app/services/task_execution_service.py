from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from sqlmodel import select
from sqlmodel import Session

from app import crud
from app.db import session_scope
from app.models import BackupScheduleRunItem
from app.platforms import platforms_compatible
from app.services import task_observability_service, task_state_service
from app.services.alert_service import check_and_alert
from app.services.backup_service import backup_device, normalize_commands
from app.services.ftp_service import upload_backup_to_ftp
from app.services.netmiko_client import NetmikoClientError
from app.services.s3_service import upload_backup_to_s3


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackupExecutionContext:
    record_id: UUID
    device_id: int
    task_id: str
    request_id: str
    device_name: str
    device_host: str
    device_port: int
    device_login_method: str
    device_encoding: str
    device_platform: str
    effective_template_id: int | None
    template_commands: str | None
    secrets: dict[str, str | None]
    run_id: UUID | None = None


@dataclass(slots=True)
class BackupRunResult:
    config_text: str


@dataclass(slots=True)
class BackupFinalizeResult:
    response: dict[str, object]
    should_retry: bool = False
    retry_countdown: int | None = None


class BackupExecutionAborted(Exception):
    def __init__(self, response: dict[str, object]):
        super().__init__(str(response))
        self.response = response


def _abort_backup_execution(
    session: Session,
    *,
    record_id: UUID,
    device_id: int,
    task_id: str,
    request_id: str,
    failure_type: str,
    error_message: str,
    reason: str,
    template_id: int | None = None,
    skip_email: bool,
) -> None:
    task_observability_service.log_task_event(
        logger,
        level="warning",
        event="backup_record_task_aborted",
        task_id=task_id,
        record_id=str(record_id),
        request_id=request_id,
        failure_type=failure_type,
        device_id=device_id,
        template_id=template_id,
    )
    record = crud.finish_backup_record(
        session,
        record_id=record_id,
        success=False,
        config_text=None,
        error_message=error_message,
        failure_type=failure_type,
    )
    if record:
        check_and_alert(session, record, skip_email=skip_email)
    task_observability_service.flush_task_event_buffer(logger=logger, force=True)
    raise BackupExecutionAborted({"ok": False, "reason": reason, "record_id": str(record_id)})


def load_backup_execution_context(
    session: Session,
    *,
    record_id: UUID,
    device_id: int,
    template_id: int | None,
    task_id: str,
    request_id: str,
    skip_email: bool,
) -> BackupExecutionContext:
    device = crud.get_device(session, device_id)
    if device is None:
        _abort_backup_execution(
            session,
            record_id=record_id,
            device_id=device_id,
            task_id=task_id,
            request_id=request_id,
            failure_type="DEVICE_NOT_FOUND",
            error_message="Device not found",
            reason="device_not_found",
            skip_email=skip_email,
        )

    effective_template_id = template_id or getattr(device, "default_template_id", None)
    template = crud.get_template(session, effective_template_id) if effective_template_id else None
    if effective_template_id and template is None:
        _abort_backup_execution(
            session,
            record_id=record_id,
            device_id=device_id,
            task_id=task_id,
            request_id=request_id,
            failure_type="TEMPLATE_NOT_FOUND",
            error_message="Template not found",
            reason="template_not_found",
            template_id=effective_template_id,
            skip_email=skip_email,
        )

    if template is not None and not platforms_compatible(template.platform, device.platform):
        _abort_backup_execution(
            session,
            record_id=record_id,
            device_id=device_id,
            task_id=task_id,
            request_id=request_id,
            failure_type="PLATFORM_MISMATCH",
            error_message="Template platform mismatch",
            reason="platform_mismatch",
            template_id=effective_template_id,
            skip_email=skip_email,
        )

    crud.update_backup_record_status(
        session,
        record_id=record_id,
        status=task_state_service.BACKUP_RECORD_STATUS_RUNNING,
    )
    run_id = session.exec(
        select(BackupScheduleRunItem.run_id)
        .where(BackupScheduleRunItem.backup_id == record_id)
        .limit(1)
    ).first()
    return BackupExecutionContext(
        record_id=record_id,
        run_id=run_id,
        device_id=device_id,
        task_id=task_id,
        request_id=request_id,
        device_name=str(device.name or ""),
        device_host=str(device.host or ""),
        device_port=int(getattr(device, "port", 22) or 22),
        device_login_method=str(getattr(device, "login_method", "ssh") or "ssh"),
        device_encoding=str(getattr(device, "encoding", "utf-8") or "utf-8"),
        device_platform=str(getattr(device, "platform", "") or ""),
        effective_template_id=effective_template_id,
        template_commands=str(template.commands) if template and getattr(template, "commands", None) is not None else None,
        secrets=crud.get_device_secrets(session, device),
    )

def run_backup_execution(context: BackupExecutionContext) -> BackupRunResult:
    if not context.secrets.get("username"):
        raise RuntimeError("Missing credential for device")

    command_count = len(normalize_commands(context.template_commands or ""))
    task_observability_service.log_task_event(
        logger,
        level="info",
        event="backup_record_connection_started",
        task_id=context.task_id,
        record_id=str(context.record_id),
        run_id=str(context.run_id or ""),
        request_id=context.request_id,
        device_id=context.device_id,
        host=context.device_host,
        port=context.device_port,
        login_method=context.device_login_method,
        platform=context.device_platform,
        command_count=command_count,
        encoding=context.device_encoding,
        template_id=context.effective_template_id,
    )

    def log_netmiko_progress(event: str, details: dict[str, object]) -> None:
        level = "warning" if event.endswith("_failed") else "info"
        task_observability_service.log_task_event(
            logger,
            level=level,
            event=event,
            task_id=context.task_id,
            record_id=str(context.record_id),
            run_id=str(context.run_id or ""),
            request_id=context.request_id,
            device_id=context.device_id,
            **dict(details or {}),
        )

    config_text = backup_device(
        host=context.device_host,
        port=context.device_port,
        login_method=context.device_login_method,
        encoding=context.device_encoding,
        platform=context.device_platform,
        username=context.secrets["username"],
        password=context.secrets["password"],
        enable_password=context.secrets["enable_password"],
        template_commands=context.template_commands,
        progress_callback=log_netmiko_progress,
    )
    task_observability_service.log_task_event(
        logger,
        level="info",
        event="backup_record_collection_completed",
        task_id=context.task_id,
        record_id=str(context.record_id),
        run_id=str(context.run_id or ""),
        request_id=context.request_id,
        device_id=context.device_id,
        line_count=len((config_text or "").splitlines()),
        content_bytes=len((config_text or "").encode("utf-8")),
    )
    return BackupRunResult(config_text=config_text)


def finalize_backup_execution(
    *,
    context: BackupExecutionContext,
    skip_email: bool,
    duration: float,
    result: BackupRunResult | None = None,
    error: Exception | None = None,
    retries_done: int = 0,
    max_retries: int = 3,
    backoff_base: int = 10,
    is_retryable_failure: Callable[[Exception, str], bool] | None = None,
    build_retry_countdown: Callable[[int, int], int] | None = None,
) -> BackupFinalizeResult:
    if error is None and result is None:
        raise ValueError("result and error cannot both be None")

    if result is not None:
        with session_scope() as session:
            record = crud.finish_backup_record(
                session,
                record_id=context.record_id,
                success=True,
                config_text=result.config_text,
                error_message=None,
                duration_seconds=duration,
                failure_type=None,
            )
            if record:
                finished_at = record.finished_at or record.started_at
                if crud.get_setting(session, key="s3_enabled") == "1":
                    s3_endpoint = (crud.get_setting(session, key="s3_endpoint") or "").strip()
                    s3_bucket = (crud.get_setting(session, key="s3_bucket") or "").strip().strip("/")
                    s3_prefix = (crud.get_setting(session, key="s3_prefix") or "backups").strip().strip("/")
                    task_observability_service.log_task_event(
                        logger,
                        level="info",
                        event="backup_record_storage_upload_started",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        storage_type="S3",
                        endpoint=s3_endpoint,
                        bucket=s3_bucket,
                        prefix=s3_prefix,
                        content_bytes=len((result.config_text or "").encode("utf-8")),
                    )
                    s3_uploaded = upload_backup_to_s3(
                        session=session,
                        device_name=context.device_name,
                        host=context.device_host,
                        config_text=result.config_text,
                        finished_at=finished_at,
                    )
                    task_observability_service.log_task_event(
                        logger,
                        level="info" if s3_uploaded else "warning",
                        event="backup_record_storage_upload",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        storage_type="S3",
                        success=s3_uploaded,
                        content_bytes=len((result.config_text or "").encode("utf-8")),
                    )
                if crud.get_setting(session, key="ftp_enabled") == "1":
                    ftp_host = (crud.get_setting(session, key="ftp_host") or "").strip()
                    ftp_port = (crud.get_setting(session, key="ftp_port") or "21").strip()
                    ftp_base_dir = (crud.get_setting(session, key="ftp_base_dir") or "").strip()
                    ftp_passive = (crud.get_setting(session, key="ftp_passive") or "1").strip()
                    ftp_timeout = (crud.get_setting(session, key="ftp_timeout") or "15").strip()
                    ftp_encoding = (crud.get_setting(session, key="ftp_encoding") or "utf-8").strip()
                    task_observability_service.log_task_event(
                        logger,
                        level="info",
                        event="backup_record_storage_upload_started",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        storage_type="FTP",
                        host=ftp_host,
                        port=ftp_port,
                        base_dir=ftp_base_dir,
                        passive=ftp_passive,
                        timeout=ftp_timeout,
                        encoding=ftp_encoding,
                        content_bytes=len((result.config_text or "").encode("utf-8")),
                    )
                    ftp_uploaded = upload_backup_to_ftp(
                        session=session,
                        device_name=context.device_name,
                        host=context.device_host,
                        config_text=result.config_text,
                        finished_at=finished_at,
                    )
                    task_observability_service.log_task_event(
                        logger,
                        level="info" if ftp_uploaded else "warning",
                        event="backup_record_storage_upload",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        storage_type="FTP",
                        success=ftp_uploaded,
                        content_bytes=len((result.config_text or "").encode("utf-8")),
                    )
                task_observability_service.log_task_event(
                    logger,
                    level="info",
                    event="backup_record_task_succeeded",
                    task_id=context.task_id,
                    record_id=str(context.record_id),
                    run_id=str(context.run_id or ""),
                    request_id=context.request_id,
                    device_id=context.device_id,
                    duration_seconds=round(duration, 3),
                )
                if not skip_email:
                    task_observability_service.log_task_event(
                        logger,
                        level="info",
                        event="backup_record_alert_check_started",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        skip_email=skip_email,
                        result="success",
                    )
                    alert_result = check_and_alert(session, record, skip_email=skip_email)
                    task_observability_service.log_task_event(
                        logger,
                        level="info",
                        event="backup_record_alert_check_completed",
                        task_id=context.task_id,
                        record_id=str(context.record_id),
                        run_id=str(context.run_id or ""),
                        request_id=context.request_id,
                        device_id=context.device_id,
                        skip_email=skip_email,
                        result="success",
                        **dict(alert_result or {}),
                    )

        if not record:
            task_observability_service.log_task_event(
                logger,
                level="info",
                event="backup_record_task_succeeded",
                task_id=context.task_id,
                record_id=str(context.record_id),
                run_id=str(context.run_id or ""),
                request_id=context.request_id,
                device_id=context.device_id,
                duration_seconds=round(duration, 3),
            )
        task_observability_service.flush_task_event_buffer(logger=logger, force=True)
        return BackupFinalizeResult(response={"ok": True, "record_id": str(context.record_id)})

    exc = error
    if isinstance(exc, SoftTimeLimitExceeded):
        failure_type = "TIME_LIMIT"
    else:
        failure_type = getattr(exc, "failure_type", "UNKNOWN") if isinstance(exc, NetmikoClientError) else "UNKNOWN"

    if (
        is_retryable_failure is not None
        and build_retry_countdown is not None
        and retries_done < max_retries
        and is_retryable_failure(exc, failure_type)
    ):
        task_observability_service.log_task_event(
            logger,
            level="warning",
            event="backup_record_task_retry_scheduled",
            task_id=context.task_id,
            record_id=str(context.record_id),
            run_id=str(context.run_id or ""),
            request_id=context.request_id,
            failure_type=failure_type,
            device_id=context.device_id,
            retries_done=retries_done,
            max_retries=max_retries,
        )
        task_observability_service.flush_task_event_buffer(logger=logger, force=True)
        return BackupFinalizeResult(
            response={
                "ok": False,
                "record_id": str(context.record_id),
                "error": str(exc),
                "failure_type": failure_type,
            },
            should_retry=True,
            retry_countdown=build_retry_countdown(retries_done, backoff_base),
        )

    with session_scope() as session:
        record = crud.finish_backup_record(
            session,
            record_id=context.record_id,
            success=False,
            config_text=None,
            error_message=str(exc),
            duration_seconds=duration,
            failure_type=failure_type,
        )
        if record:
            task_observability_service.log_task_event(
                logger,
                level="warning",
                event="backup_record_task_failed",
                task_id=context.task_id,
                record_id=str(context.record_id),
                run_id=str(context.run_id or ""),
                request_id=context.request_id,
                failure_type=failure_type,
                device_id=context.device_id,
                error=str(exc),
                duration_seconds=round(duration, 3),
            )
            if not skip_email:
                task_observability_service.log_task_event(
                    logger,
                    level="info",
                    event="backup_record_alert_check_started",
                    task_id=context.task_id,
                    record_id=str(context.record_id),
                    run_id=str(context.run_id or ""),
                    request_id=context.request_id,
                    device_id=context.device_id,
                    skip_email=skip_email,
                    result="failure",
                )
                alert_result = check_and_alert(session, record, skip_email=skip_email)
                task_observability_service.log_task_event(
                    logger,
                    level="info",
                    event="backup_record_alert_check_completed",
                    task_id=context.task_id,
                    record_id=str(context.record_id),
                    run_id=str(context.run_id or ""),
                    request_id=context.request_id,
                    device_id=context.device_id,
                    skip_email=skip_email,
                    result="failure",
                    **dict(alert_result or {}),
                )

    if not record:
        task_observability_service.log_task_event(
            logger,
            level="warning",
            event="backup_record_task_failed",
            task_id=context.task_id,
            record_id=str(context.record_id),
            run_id=str(context.run_id or ""),
            request_id=context.request_id,
            failure_type=failure_type,
            device_id=context.device_id,
            error=str(exc),
            duration_seconds=round(duration, 3),
        )
    task_observability_service.flush_task_event_buffer(logger=logger, force=True)

    return BackupFinalizeResult(
        response={
            "ok": False,
            "record_id": str(context.record_id),
            "error": str(exc),
            "failure_type": failure_type,
        }
    )
