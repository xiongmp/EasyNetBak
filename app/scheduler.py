from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset, parse_timezone_offset_to_minutes
from app.db import session_scope
from app.models import BackupSchedule, BackupScheduleRun
from app.platforms import platforms_compatible


_scheduler: BackgroundScheduler | None = None
_scheduler_offset_minutes: int | None = None


def start_scheduler(*, offset_minutes: int) -> None:
    global _scheduler
    global _scheduler_offset_minutes
    offset_minutes = int(offset_minutes)
    if _scheduler is None or _scheduler_offset_minutes != offset_minutes:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
        _scheduler = BackgroundScheduler(timezone=timezone(timedelta(minutes=offset_minutes)))
        _scheduler_offset_minutes = offset_minutes
    if not _scheduler.running:
        _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    global _scheduler_offset_minutes
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    _scheduler_offset_minutes = None


def _normalize_targets_text(value: str | None) -> str:
    return "\n".join([line.strip() for line in (value or "").splitlines() if line.strip()])


def resolve_device_ids_from_targets(session, *, targets: str | None) -> list[int]:
    raw = _normalize_targets_text(targets)
    if not raw:
        return [int(d.id) for d in crud.list_devices(session) if d.id]

    devices = crud.list_devices(session)
    by_id = {int(d.id): d for d in devices if d.id}
    by_host = {str(d.host).strip(): int(d.id) for d in devices if d.id and d.host}

    groups = crud.list_groups(session)
    group_name_to_id = {g.name.strip().lower(): int(g.id) for g in groups if g.id and g.name}

    picked: set[int] = set()
    for token in raw.splitlines():
        t = token.strip()
        if not t:
            continue
        lower = t.lower()
        if lower == "all":
            picked.update(by_id.keys())
            continue
        if lower.isdigit():
            did = int(lower)
            if did in by_id:
                picked.add(did)
            continue
        if ":" not in t:
            if t in by_host:
                picked.add(by_host[t])
            continue
        prefix, value = t.split(":", 1)
        prefix = prefix.strip().lower()
        value = value.strip()
        if not value:
            continue
        if prefix == "device" and value.isdigit():
            did = int(value)
            if did in by_id:
                picked.add(did)
        elif prefix == "host":
            did = by_host.get(value)
            if did:
                picked.add(int(did))
        elif prefix == "group":
            gid = int(value) if value.isdigit() else group_name_to_id.get(value.strip().lower())
            if gid:
                picked.update([int(d.id) for d in devices if d.id and int(d.group_id or 0) == int(gid)])
        elif prefix == "platform":
            picked.update([int(d.id) for d in devices if d.id and (d.platform or "").strip() == value])

    return sorted(picked)


def resolve_schedule_device_ids(session, *, schedule: BackupSchedule) -> list[int]:
    return resolve_device_ids_from_targets(session, targets=schedule.targets)


def _effective_template_id(session, *, device_id: int) -> int | None:
    device = crud.get_device(session, device_id)
    if device is None:
        return None
    effective_template_id = int(getattr(device, "default_template_id", 0) or 0)
    if not effective_template_id:
        return None
    tpl = crud.get_template(session, effective_template_id)
    if tpl is None:
        return None
    if not platforms_compatible(tpl.platform, device.platform):
        return None
    return int(effective_template_id)


def plan_schedule_run(*, schedule_id: int, trigger: str) -> tuple[UUID, list[tuple[int, UUID, int | None]]]:
    with session_scope() as session:
        schedule = crud.get_schedule(session, schedule_id)
        if schedule is None:
            raise RuntimeError("Schedule not found")
        device_ids = resolve_schedule_device_ids(session, schedule=schedule)
        run = crud.create_schedule_run(session, schedule_id=schedule_id, trigger=trigger, total_devices=len(device_ids))
        run_id = UUID(str(run.id))
        jobs: list[tuple[int, UUID, int | None]] = []
        for did in device_ids:
            tpl_id = _effective_template_id(session, device_id=did)
            record = crud.create_backup_record(session, device_id=did, template_id=tpl_id)
            crud.add_schedule_run_item(
                session,
                run_id=run_id,
                schedule_id=schedule_id,
                backup_id=record.id,
                device_id=did,
            )
            jobs.append((did, record.id, tpl_id))
        return run_id, jobs


def execute_schedule_run(*, run_id: UUID, jobs: list[tuple[int, UUID, int | None]]) -> bool:
    from app.celery_tasks import enqueue_schedule_run

    if enqueue_schedule_run(run_id=run_id, jobs=jobs):
        return True

    with session_scope() as session:
        crud.finish_schedule_run(
            session,
            run_id=run_id,
            success_count=0,
            fail_count=0,
            error_message="CELERY_ENQUEUE_FAILED",
        )
    return False


def run_cleanup() -> None:
    """运行过期的备份清理任务和 Webshell 录像清理任务"""
    with session_scope() as session:
        # 清理备份
        retention_days_str = crud.get_setting(session, key="backup_retention_days")
        try:
            days = int(retention_days_str or "90")
        except (ValueError, TypeError):
            days = 90
        
        if days > 0:
            count = crud.cleanup_old_backups(session, days)
            # 这里可以根据需要添加日志或告警

        # 清理 Webshell 录像
        webshell_retention_days_str = crud.get_setting(session, key="webshell_record_retention_days")
        try:
            webshell_days = int(webshell_retention_days_str or "30")
        except (ValueError, TypeError):
            webshell_days = 30
        
        if webshell_days > 0:
            webshell_count = crud.cleanup_old_webshell_records(session, webshell_days)
            # 这里可以根据需要添加日志或告警


def sync_scheduler_from_db() -> None:
    with session_scope() as session:
        tz_str = crud.get_setting(session, key="timezone_offset")
        schedules = crud.list_schedules(session)

    tz_offset = normalize_timezone_offset(tz_str, default=settings.timezone_offset)
    offset_minutes = parse_timezone_offset_to_minutes(tz_offset) or 0
    start_scheduler(offset_minutes=offset_minutes)
    if _scheduler is None:
        return

    _scheduler.remove_all_jobs()

    # 添加定时清理任务，每天凌晨 3:00 执行
    _scheduler.add_job(
        run_cleanup,
        CronTrigger.from_crontab("0 3 * * *", timezone=_scheduler.timezone),
        id="backup_cleanup",
        replace_existing=True,
    )

    for s in schedules:
        if not s.id or not s.enabled:
            continue
        sid = int(s.id)
        crontab = (s.crontab or "").strip()
        if not crontab:
            continue

        def _job(schedule_id: int = sid) -> None:
            from app.celery_tasks import celery_enabled

            if not celery_enabled():
                return
            run_id, jobs = plan_schedule_run(schedule_id=schedule_id, trigger="cron")
            execute_schedule_run(run_id=run_id, jobs=jobs)

        _scheduler.add_job(
            _job,
            CronTrigger.from_crontab(crontab, timezone=_scheduler.timezone),
            id=f"schedule_{sid}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


def plan_bulk_backup_run(session, device_ids: list[int], trigger: str = "manual") -> tuple[UUID, list[tuple[int, UUID, int | None]]]:
    """为手动批量备份计划一次运行记录"""
    run = crud.create_schedule_run(session, schedule_id=0, trigger=trigger, total_devices=len(device_ids))
    run_id = UUID(str(run.id))
    jobs: list[tuple[int, UUID, int | None]] = []
    for did in device_ids:
        tpl_id = _effective_template_id(session, device_id=did)
        record = crud.create_backup_record(session, device_id=did, template_id=tpl_id)
        crud.add_schedule_run_item(
            session,
            run_id=run_id,
            schedule_id=0,
            backup_id=record.id,
            device_id=did,
        )
        jobs.append((did, record.id, tpl_id))
    return run_id, jobs


def ensure_default_schedule_from_legacy_settings(*, enabled: bool, crontab: str) -> None:
    with session_scope() as session:
        existing = session.exec(select(BackupSchedule.id).limit(1)).first()
        if existing is not None:
            return
        schedule = BackupSchedule(
            name="默认定时备份",
            crontab=(crontab or "").strip() or "0 2 * * *",
            enabled=bool(enabled),
            targets="all",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        crud.create_schedule(session, schedule=schedule)
