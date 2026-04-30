from __future__ import annotations

from uuid import UUID

from celery import Task


def dispatch_backup_record_task(
    task_func: Task,
    *,
    record_id: UUID,
    device_id: int,
    template_id: int | None,
    skip_email: bool,
    time_limit: int,
) -> None:
    task_func.apply_async(
        args=[str(record_id), int(device_id), int(template_id) if template_id is not None else None],
        kwargs={"skip_email": bool(skip_email)},
        task_id=str(record_id),
        time_limit=time_limit if time_limit > 0 else None,
        soft_time_limit=max(1, time_limit - 10) if time_limit > 10 else None,
    )


def dispatch_schedule_finalizer(
    task_func: Task,
    *,
    run_id: UUID,
    backup_ids: list[str],
    countdown: int,
) -> None:
    task_func.apply_async(
        args=[str(run_id), backup_ids],
        countdown=countdown,
        task_id=f"finalize-{str(run_id)}",
    )
