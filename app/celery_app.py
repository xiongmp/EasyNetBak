from __future__ import annotations

from celery import Celery

from app.core.settings import settings


def _normalize_optional_backend(value: str) -> str | None:
    v = (value or "").strip()
    return v or None


celery_app = Celery("network_backup")

conf: dict[str, object] = {
    "task_track_started": True,
    "task_ignore_result": False,
}

broker = (settings.celery.broker_url or "").strip()
if broker:
    conf["broker_url"] = broker

backend = _normalize_optional_backend(settings.celery.result_backend)
if backend:
    conf["result_backend"] = backend

celery_app.conf.update(conf)

soft_limit = int(settings.celery.task_soft_time_limit_seconds or 0)
hard_limit = int(settings.celery.task_time_limit_seconds or 0)
if soft_limit > 0:
    celery_app.conf.task_soft_time_limit = soft_limit
if hard_limit > 0:
    celery_app.conf.task_time_limit = hard_limit
