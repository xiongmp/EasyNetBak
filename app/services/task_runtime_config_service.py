from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app import crud
from app.core.settings import settings
from app.db import session_scope
from app.i18n import translate

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BackupTaskRuntimeConfig:
    max_slots: int
    max_retries: int
    backoff_base: int
    time_limit: int


def _log_degraded_runtime_config(
    *,
    setting_key: str,
    raw_value: str | None,
    fallback_value: int,
    reason: str,
    component: str = "task_runtime_config",
) -> None:
    logger.warning(
        json.dumps(
            {
                "status": "degraded",
                "component": component,
                "setting_key": setting_key,
                "raw_value": raw_value,
                "fallback_value": fallback_value,
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )


def _parse_int_setting(
    *,
    setting_key: str,
    raw_value: str | None,
    fallback_value: int,
    allow_zero: bool = True,
) -> int:
    if raw_value is None or str(raw_value).strip() == "":
        return int(fallback_value)

    parsed = int(str(raw_value).strip())
    if not allow_zero and parsed == 0:
        raise ValueError(f"{setting_key} cannot be 0")
    return parsed


def load_backup_task_runtime_config() -> BackupTaskRuntimeConfig:
    max_slots = 10
    max_retries = int(settings.celery.backup_max_retries)
    backoff_base = int(settings.celery.backup_retry_backoff_seconds)
    time_limit = int(settings.celery.task_time_limit_seconds)

    try:
        with session_scope() as session:
            val_concurrent = crud.get_setting(session, key="max_concurrent_tasks")
            try:
                max_slots = _parse_int_setting(
                    setting_key="max_concurrent_tasks",
                    raw_value=val_concurrent,
                    fallback_value=max_slots,
                )
            except (TypeError, ValueError) as exc:
                _log_degraded_runtime_config(
                    setting_key="max_concurrent_tasks",
                    raw_value=val_concurrent,
                    fallback_value=max_slots,
                    reason=str(exc),
                )

            val_retries = crud.get_setting(session, key="backup_max_retries")
            try:
                max_retries = _parse_int_setting(
                    setting_key="backup_max_retries",
                    raw_value=val_retries,
                    fallback_value=max_retries,
                )
            except (TypeError, ValueError) as exc:
                _log_degraded_runtime_config(
                    setting_key="backup_max_retries",
                    raw_value=val_retries,
                    fallback_value=max_retries,
                    reason=str(exc),
                )

            val_backoff = crud.get_setting(session, key="backup_retry_backoff")
            try:
                backoff_base = _parse_int_setting(
                    setting_key="backup_retry_backoff",
                    raw_value=val_backoff,
                    fallback_value=backoff_base,
                )
            except (TypeError, ValueError) as exc:
                _log_degraded_runtime_config(
                    setting_key="backup_retry_backoff",
                    raw_value=val_backoff,
                    fallback_value=backoff_base,
                    reason=str(exc),
                )

            val_timeout = crud.get_setting(session, key="task_time_limit")
            try:
                time_limit = _parse_int_setting(
                    setting_key="task_time_limit",
                    raw_value=val_timeout,
                    fallback_value=time_limit,
                    allow_zero=True,
                )
            except (TypeError, ValueError) as exc:
                _log_degraded_runtime_config(
                    setting_key="task_time_limit",
                    raw_value=val_timeout,
                    fallback_value=time_limit,
                    reason=str(exc),
                )
    except SQLAlchemyError as exc:
        _log_degraded_runtime_config(
            setting_key="task_runtime_config",
            raw_value=None,
            fallback_value=max_slots,
            reason=str(exc),
            component="database",
        )

    return BackupTaskRuntimeConfig(
        max_slots=max_slots,
        max_retries=max_retries,
        backoff_base=backoff_base,
        time_limit=time_limit,
    )


def load_task_time_limit() -> int:
    time_limit = int(settings.celery.task_time_limit_seconds)
    try:
        with session_scope() as session:
            val_timeout = crud.get_setting(session, key="task_time_limit")
            try:
                time_limit = _parse_int_setting(
                    setting_key="task_time_limit",
                    raw_value=val_timeout,
                    fallback_value=time_limit,
                    allow_zero=True,
                )
            except (TypeError, ValueError) as exc:
                _log_degraded_runtime_config(
                    setting_key="task_time_limit",
                    raw_value=val_timeout,
                    fallback_value=time_limit,
                    reason=str(exc),
                )
    except SQLAlchemyError as exc:
        _log_degraded_runtime_config(
            setting_key="task_time_limit",
            raw_value=None,
            fallback_value=time_limit,
            reason=str(exc),
            component="database",
        )
    return time_limit


def get_task_runtime_degradation_status(locale: str | None = None) -> list[dict[str, str]]:
    degraded: list[dict[str, str]] = []
    broker_url = (settings.celery.broker_url or "").strip()
    if not broker_url:
        degraded.append(
            {
                "component": "celery",
                "status": "degraded",
                "message": translate(locale, "task.health.broker_not_configured"),
            }
        )
    return degraded
