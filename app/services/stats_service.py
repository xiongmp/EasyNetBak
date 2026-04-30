from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, func, select

from app import crud
from app.models import BackupRecord
from app.services import task_state_service


def get_system_stats_payload(session: Session) -> dict[str, Any]:
    total_devices = crud.count_devices(session, q=None, platform=None, group_id=None)
    unreachable_devices = crud.count_devices(
        session,
        q=None,
        platform=None,
        group_id=None,
        reachability_status=False,
    )

    now = datetime.utcnow()
    last_24h = datetime.fromtimestamp(now.timestamp() - 86400)
    stmt_failed_backups = select(func.count()).select_from(BackupRecord).where(
        BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_FAILED,
        BackupRecord.started_at >= last_24h,
    )
    failed_backups_24h = session.exec(stmt_failed_backups).one()

    return {
        "total_devices": int(total_devices),
        "unreachable_devices": int(unreachable_devices),
        "failed_backups_24h": int(failed_backups_24h),
    }
