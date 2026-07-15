from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import redis
from redis import asyncio as redis_asyncio

from app.core.settings import settings
from app.models import TaskEvent


logger = logging.getLogger(__name__)
TASK_EVENT_BUS_CHANNEL = "nb:task-events:v1"
_PUBLISH_CLIENT_LOCK = threading.Lock()
_PUBLISH_CLIENT: redis.Redis | None = None


def _broker_url() -> str:
    return (settings.celery.broker_url or "").strip()


def event_bus_enabled() -> bool:
    url = _broker_url()
    if not url:
        return False
    try:
        scheme = urlparse(url).scheme.lower()
    except Exception:
        return False
    return scheme in {"redis", "rediss"}


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


def _get_publish_client() -> redis.Redis | None:
    global _PUBLISH_CLIENT
    if not event_bus_enabled():
        return None
    with _PUBLISH_CLIENT_LOCK:
        if _PUBLISH_CLIENT is not None:
            return _PUBLISH_CLIENT
        try:
            _PUBLISH_CLIENT = redis.from_url(_broker_url())
        except (redis.RedisError, ValueError, TypeError) as exc:
            _log_degraded_component(component="task_event_bus_publish", reason=str(exc), broker_url=_broker_url())
            _PUBLISH_CLIENT = None
        return _PUBLISH_CLIENT


def serialize_task_event_model(task_event: TaskEvent) -> dict[str, Any]:
    try:
        details = json.loads(task_event.details or "{}")
        if not isinstance(details, dict):
            details = {}
    except Exception:
        details = {}
    return {
        "type": "task_event",
        "event_id": int(task_event.id or 0),
        "event": str(task_event.event or ""),
        "task_id": str(task_event.task_id or ""),
        "record_id": str(task_event.record_id or ""),
        "run_id": str(task_event.run_id or ""),
        "request_id": str(task_event.request_id or ""),
        "device_id": int(task_event.device_id or 0) if task_event.device_id is not None else None,
        "failure_type": str(task_event.failure_type or ""),
        "storage_type": str(task_event.storage_type or ""),
        "success": task_event.success,
        "retries_done": int(task_event.retries_done or 0) if task_event.retries_done is not None else None,
        "max_retries": int(task_event.max_retries or 0) if task_event.max_retries is not None else None,
        "details": details,
        "created_at": task_event.created_at.isoformat() if task_event.created_at else "",
    }


def publish_task_events(events: list[dict[str, Any]]) -> int:
    if not events or not event_bus_enabled():
        return 0
    client = _get_publish_client()
    if client is None:
        return 0
    published = 0
    for payload in events:
        try:
            client.publish(TASK_EVENT_BUS_CHANNEL, json.dumps(payload, ensure_ascii=False))
            published += 1
        except redis.RedisError as exc:
            _log_degraded_component(
                component="task_event_bus_publish",
                reason=str(exc),
                channel=TASK_EVENT_BUS_CHANNEL,
                payload_type=str(payload.get("type") or ""),
            )
            break
    return published


def publish_task_state_hint(
    *,
    run_id: str | None = None,
    record_id: str | None = None,
    event: str = "task_state_hint",
    details: dict[str, Any] | None = None,
) -> int:
    payload = {
        "type": "task_state_hint",
        "event": str(event or "task_state_hint"),
        "run_id": str(run_id or ""),
        "record_id": str(record_id or ""),
        "details": dict(details or {}),
    }
    return publish_task_events([payload])


async def pump_task_events(
    handler: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    stop_event: asyncio.Event,
) -> None:
    if not event_bus_enabled():
        return
    while not stop_event.is_set():
        client = None
        pubsub = None
        try:
            client = redis_asyncio.from_url(_broker_url())
            pubsub = client.pubsub()
            await pubsub.subscribe(TASK_EVENT_BUS_CHANNEL)
            while not stop_event.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.1)
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="ignore")
                if not isinstance(data, str) or not data.strip():
                    continue
                try:
                    payload = json.loads(data)
                except Exception:
                    logger.debug("Skip invalid task event bus payload", exc_info=True)
                    continue
                if isinstance(payload, dict):
                    await handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log_degraded_component(component="task_event_bus_subscribe", reason=str(exc), channel=TASK_EVENT_BUS_CHANNEL)
            await asyncio.sleep(2.0)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(TASK_EVENT_BUS_CHANNEL)
                except Exception:
                    pass
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
