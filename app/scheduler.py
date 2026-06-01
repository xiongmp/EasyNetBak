from __future__ import annotations

from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset, parse_timezone_offset_to_minutes
from app.db import session_scope
from app.models import BackupSchedule
from app.services import task_orchestration_service
import logging


logger = logging.getLogger(__name__)


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


def resolve_device_ids_from_targets(
    session,
    *,
    targets: str | None,
    allowed_group_ids: list[int] | None = None,
) -> list[int]:
    raw = _normalize_targets_text(targets)
    if not raw:
        # 如果 targets 明确为 "all" 字符串，则返回所有
        if targets and targets.strip().lower() == "all":
            return [int(d.id) for d in crud.list_devices(session) if d.id]
        # 否则，如果为空（且不是显式的 all），则返回空列表（表示未选择任何目标）
        return []

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
                subtree_ids = set(crud.expand_group_ids(session, [int(gid)], include_special_ids=False))
                picked.update(
                    [
                        int(d.id)
                        for d in devices
                        if d.id and int(d.group_id or 0) in subtree_ids
                    ]
                )
        elif prefix == "platform":
            picked.update([int(d.id) for d in devices if d.id and (d.platform or "").strip() == value])

    if allowed_group_ids is None:
        return sorted(picked)

    allowed_set = set(allowed_group_ids)
    out: list[int] = []
    for device_id in sorted(picked):
        device = by_id.get(int(device_id))
        if device is None:
            continue
        group_id = int(getattr(device, "group_id", 0) or 0)
        if group_id in allowed_set or (group_id == 0 and (-1 in allowed_set or 0 in allowed_set)):
            out.append(int(device_id))
    return out


def resolve_schedule_device_ids(
    session,
    *,
    schedule: BackupSchedule,
    allowed_group_ids: list[int] | None = None,
) -> list[int]:
    return resolve_device_ids_from_targets(
        session,
        targets=schedule.targets,
        allowed_group_ids=allowed_group_ids,
    )


def run_cleanup() -> None:
    """运行过期的备份清理任务和日志清理任务"""
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

        # 清理操作日志
        audit_log_retention_days_str = crud.get_setting(session, key="audit_log_retention_days")
        try:
            audit_days = int(audit_log_retention_days_str or "180")
        except (ValueError, TypeError):
            audit_days = 180
        
        if audit_days > 0:
            audit_count = crud.cleanup_old_audit_logs(session, audit_days)

        # 清理登录日志
        login_log_retention_days_str = crud.get_setting(session, key="login_log_retention_days")
        try:
            login_days = int(login_log_retention_days_str or "180")
        except (ValueError, TypeError):
            login_days = 180
        
        if login_days > 0:
            login_count = crud.cleanup_old_login_logs(session, login_days)

        # TaskEvent 固定默认只保留最近 90 天
        crud.cleanup_old_task_events(session, 90)


def sync_scheduler_from_db() -> None:
    from app.services import schedule_service

    with session_scope() as session:
        tz_str = crud.get_setting(session, key="timezone_offset")
        schedules = crud.list_schedules(session)
        legacy_named_targets = schedule_service.list_legacy_group_name_targets(session)

    tz_offset = normalize_timezone_offset(tz_str, default=settings.timezone_offset)
    offset_minutes = parse_timezone_offset_to_minutes(tz_offset) or 0
    start_scheduler(offset_minutes=offset_minutes)
    if _scheduler is None:
        return

    _scheduler.remove_all_jobs()

    if legacy_named_targets:
        for item in legacy_named_targets:
            legacy_tokens = ", ".join(
                f"{target['raw']} -> {target['normalized']}"
                for target in item.get("targets", [])
            )
            logger.warning(
                "Schedule %s(%s) still contains legacy named group targets: %s",
                item.get("schedule_name") or "",
                item.get("schedule_id") or 0,
                legacy_tokens,
            )

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
            with session_scope() as session:
                schedule = crud.get_schedule(session, schedule_id)
                if schedule is None:
                    return
                device_ids = resolve_schedule_device_ids(session, schedule=schedule)
                if not device_ids:
                    return
                run_id, jobs = task_orchestration_service.plan_schedule_run(
                    session,
                    schedule_id=schedule_id,
                    trigger="cron",
                    device_ids=device_ids,
                )
                task_orchestration_service.enqueue_schedule_run(
                    session,
                    run_id=run_id,
                    jobs=jobs,
                    skip_email=True,
                )

        _scheduler.add_job(
            _job,
            CronTrigger.from_crontab(crontab, timezone=_scheduler.timezone),
            id=f"schedule_{sid}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


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
