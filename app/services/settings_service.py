from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset


@dataclass(frozen=True, slots=True)
class SystemSettingsPayload:
    timezone_offset: str
    max_concurrent_tasks: str
    backup_max_retries: str
    backup_retry_backoff: str
    task_time_limit: str
    backup_retention_days: str
    webshell_record_retention_days: str
    audit_log_retention_days: str
    login_log_retention_days: str

    def as_dict(self) -> dict[str, str]:
        return {
            "timezone_offset": self.timezone_offset,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "backup_max_retries": self.backup_max_retries,
            "backup_retry_backoff": self.backup_retry_backoff,
            "task_time_limit": self.task_time_limit,
            "backup_retention_days": self.backup_retention_days,
            "webshell_record_retention_days": self.webshell_record_retention_days,
            "audit_log_retention_days": self.audit_log_retention_days,
            "login_log_retention_days": self.login_log_retention_days,
        }


def _normalize_int_setting(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> str:
    try:
        normalized = int(value) if value is not None else default
    except (TypeError, ValueError):
        normalized = default
    if normalized < minimum:
        normalized = minimum
    if maximum is not None and normalized > maximum:
        normalized = maximum
    return str(normalized)


def get_system_settings_payload(session: Session) -> SystemSettingsPayload:
    timezone_str = crud.get_setting(session, key="timezone_offset")
    max_concurrent = crud.get_setting(session, key="max_concurrent_tasks")
    backup_max_retries = crud.get_setting(session, key="backup_max_retries")
    backup_retry_backoff = crud.get_setting(session, key="backup_retry_backoff")
    task_time_limit = crud.get_setting(session, key="task_time_limit")
    backup_retention_days = crud.get_setting(session, key="backup_retention_days")
    webshell_record_retention_days = crud.get_setting(session, key="webshell_record_retention_days")
    audit_log_retention_days = crud.get_setting(session, key="audit_log_retention_days")
    login_log_retention_days = crud.get_setting(session, key="login_log_retention_days")

    return SystemSettingsPayload(
        timezone_offset=normalize_timezone_offset(timezone_str, default=settings.timezone_offset),
        max_concurrent_tasks=max_concurrent or "10",
        backup_max_retries=backup_max_retries if backup_max_retries is not None else str(settings.celery.backup_max_retries),
        backup_retry_backoff=backup_retry_backoff if backup_retry_backoff is not None else str(settings.celery.backup_retry_backoff_seconds),
        task_time_limit=task_time_limit if task_time_limit is not None else str(settings.celery.task_time_limit_seconds),
        backup_retention_days=backup_retention_days or "90",
        webshell_record_retention_days=webshell_record_retention_days or "30",
        audit_log_retention_days=audit_log_retention_days or "180",
        login_log_retention_days=login_log_retention_days or "180",
    )


def save_system_settings(
    session: Session,
    *,
    timezone_offset: str,
    max_concurrent_tasks: str,
    backup_max_retries: str,
    backup_retry_backoff: str,
    task_time_limit: str,
    backup_retention_days: str,
    webshell_record_retention_days: str,
    audit_log_retention_days: str,
    login_log_retention_days: str,
) -> SystemSettingsPayload:
    payload = SystemSettingsPayload(
        timezone_offset=normalize_timezone_offset(timezone_offset, default=settings.timezone_offset),
        max_concurrent_tasks=_normalize_int_setting(max_concurrent_tasks, default=10, minimum=1, maximum=100),
        backup_max_retries=_normalize_int_setting(
            backup_max_retries,
            default=int(settings.celery.backup_max_retries),
            minimum=0,
            maximum=10,
        ),
        backup_retry_backoff=_normalize_int_setting(
            backup_retry_backoff,
            default=int(settings.celery.backup_retry_backoff_seconds),
            minimum=1,
            maximum=3600,
        ),
        task_time_limit=_normalize_int_setting(
            task_time_limit,
            default=int(settings.celery.task_time_limit_seconds),
            minimum=0,
            maximum=3600,
        ),
        backup_retention_days=_normalize_int_setting(backup_retention_days, default=90, minimum=1),
        webshell_record_retention_days=_normalize_int_setting(webshell_record_retention_days, default=30, minimum=1),
        audit_log_retention_days=_normalize_int_setting(audit_log_retention_days, default=180, minimum=1),
        login_log_retention_days=_normalize_int_setting(login_log_retention_days, default=180, minimum=1),
    )

    for key, value in payload.as_dict().items():
        crud.set_setting(session, key=key, value=value)

    return payload


def build_system_settings_audit_details(payload: SystemSettingsPayload) -> str:
    return (
        f"TZ: {payload.timezone_offset}, "
        f"MaxConcurrent: {payload.max_concurrent_tasks}, "
        f"BackupRetries: {payload.backup_max_retries}, "
        f"RetryBackoff: {payload.backup_retry_backoff}, "
        f"TaskTimeLimit: {payload.task_time_limit}, "
        f"Backup Retention: {payload.backup_retention_days}, "
        f"Webshell Retention: {payload.webshell_record_retention_days}, "
        f"Audit Retention: {payload.audit_log_retention_days}, "
        f"Login Retention: {payload.login_log_retention_days}"
    )
