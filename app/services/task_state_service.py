from __future__ import annotations

BACKUP_RECORD_STATUS_PLANNED = "planned"
BACKUP_RECORD_STATUS_QUEUED = "queued"
BACKUP_RECORD_STATUS_RUNNING = "running"
BACKUP_RECORD_STATUS_CANCELLED = "cancelled"
BACKUP_RECORD_STATUS_SUCCEEDED = "succeeded"
BACKUP_RECORD_STATUS_FAILED = "failed"

BACKUP_RECORD_ACTIVE_STATUSES = (
    BACKUP_RECORD_STATUS_PLANNED,
    BACKUP_RECORD_STATUS_QUEUED,
    BACKUP_RECORD_STATUS_RUNNING,
)
BACKUP_RECORD_TERMINAL_STATUSES = (
    BACKUP_RECORD_STATUS_CANCELLED,
    BACKUP_RECORD_STATUS_SUCCEEDED,
    BACKUP_RECORD_STATUS_FAILED,
)

SCHEDULE_RUN_STATUS_PLANNED = "planned"
SCHEDULE_RUN_STATUS_DISPATCHING = "dispatching"
SCHEDULE_RUN_STATUS_RUNNING = "running"
SCHEDULE_RUN_STATUS_FINALIZING = "finalizing"
SCHEDULE_RUN_STATUS_CANCELLING = "cancelling"
SCHEDULE_RUN_STATUS_CANCELLED = "cancelled"
SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED = "partial_cancelled"
SCHEDULE_RUN_STATUS_SUCCEEDED = "succeeded"
SCHEDULE_RUN_STATUS_PARTIAL_FAILED = "partial_failed"
SCHEDULE_RUN_STATUS_FAILED = "failed"

SCHEDULE_RUN_ACTIVE_STATUSES = (
    SCHEDULE_RUN_STATUS_PLANNED,
    SCHEDULE_RUN_STATUS_DISPATCHING,
    SCHEDULE_RUN_STATUS_RUNNING,
    SCHEDULE_RUN_STATUS_FINALIZING,
    SCHEDULE_RUN_STATUS_CANCELLING,
)
SCHEDULE_RUN_TERMINAL_STATUSES = (
    SCHEDULE_RUN_STATUS_CANCELLED,
    SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED,
    SCHEDULE_RUN_STATUS_SUCCEEDED,
    SCHEDULE_RUN_STATUS_PARTIAL_FAILED,
    SCHEDULE_RUN_STATUS_FAILED,
)

_BACKUP_RECORD_STATUS_LABELS = {
    BACKUP_RECORD_STATUS_PLANNED: "待计划",
    BACKUP_RECORD_STATUS_QUEUED: "已入队",
    BACKUP_RECORD_STATUS_RUNNING: "运行中",
    BACKUP_RECORD_STATUS_CANCELLED: "已终止",
    BACKUP_RECORD_STATUS_SUCCEEDED: "成功",
    BACKUP_RECORD_STATUS_FAILED: "失败",
}

_BACKUP_RECORD_STATUS_TONES = {
    BACKUP_RECORD_STATUS_PLANNED: "info",
    BACKUP_RECORD_STATUS_QUEUED: "info",
    BACKUP_RECORD_STATUS_RUNNING: "running",
    BACKUP_RECORD_STATUS_CANCELLED: "warning",
    BACKUP_RECORD_STATUS_SUCCEEDED: "success",
    BACKUP_RECORD_STATUS_FAILED: "failed",
}

_SCHEDULE_RUN_STATUS_LABELS = {
    SCHEDULE_RUN_STATUS_PLANNED: "待计划",
    SCHEDULE_RUN_STATUS_DISPATCHING: "派发中",
    SCHEDULE_RUN_STATUS_RUNNING: "运行中",
    SCHEDULE_RUN_STATUS_FINALIZING: "收尾中",
    SCHEDULE_RUN_STATUS_CANCELLING: "终止中",
    SCHEDULE_RUN_STATUS_CANCELLED: "已终止",
    SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED: "部分终止",
    SCHEDULE_RUN_STATUS_SUCCEEDED: "全部成功",
    SCHEDULE_RUN_STATUS_PARTIAL_FAILED: "部分失败",
    SCHEDULE_RUN_STATUS_FAILED: "全部失败",
}

_SCHEDULE_RUN_STATUS_TONES = {
    SCHEDULE_RUN_STATUS_PLANNED: "info",
    SCHEDULE_RUN_STATUS_DISPATCHING: "info",
    SCHEDULE_RUN_STATUS_RUNNING: "running",
    SCHEDULE_RUN_STATUS_FINALIZING: "running",
    SCHEDULE_RUN_STATUS_CANCELLING: "warning",
    SCHEDULE_RUN_STATUS_CANCELLED: "warning",
    SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED: "warning",
    SCHEDULE_RUN_STATUS_SUCCEEDED: "success",
    SCHEDULE_RUN_STATUS_PARTIAL_FAILED: "failed",
    SCHEDULE_RUN_STATUS_FAILED: "failed",
}


def is_backup_record_active_status(status: str | None) -> bool:
    return str(status or "").strip() in BACKUP_RECORD_ACTIVE_STATUSES


def is_backup_record_terminal_status(status: str | None) -> bool:
    return str(status or "").strip() in BACKUP_RECORD_TERMINAL_STATUSES


def is_schedule_run_active_status(status: str | None) -> bool:
    return str(status or "").strip() in SCHEDULE_RUN_ACTIVE_STATUSES


def is_schedule_run_terminal_status(status: str | None) -> bool:
    return str(status or "").strip() in SCHEDULE_RUN_TERMINAL_STATUSES


def is_backup_record_pending_status(status: str | None) -> bool:
    return str(status or "").strip() in (
        BACKUP_RECORD_STATUS_PLANNED,
        BACKUP_RECORD_STATUS_QUEUED,
    )


def backup_record_terminal_status(*, success: bool) -> str:
    return BACKUP_RECORD_STATUS_SUCCEEDED if bool(success) else BACKUP_RECORD_STATUS_FAILED


def get_backup_record_status_label(status: str | None) -> str:
    normalized = str(status or "").strip()
    return _BACKUP_RECORD_STATUS_LABELS.get(normalized, normalized or "unknown")


def get_backup_record_status_tone(status: str | None) -> str:
    normalized = str(status or "").strip()
    return _BACKUP_RECORD_STATUS_TONES.get(normalized, "info")


def get_schedule_run_status_label(status: str | None) -> str:
    normalized = str(status or "").strip()
    return _SCHEDULE_RUN_STATUS_LABELS.get(normalized, normalized or "unknown")


def get_schedule_run_status_tone(status: str | None) -> str:
    normalized = str(status or "").strip()
    return _SCHEDULE_RUN_STATUS_TONES.get(normalized, "info")


def derive_schedule_run_terminal_status(
    *,
    total_devices: int,
    success_count: int,
    fail_count: int,
    cancelled_count: int = 0,
    unfinished_count: int = 0,
) -> str:
    total_devices = max(0, int(total_devices or 0))
    success_count = max(0, int(success_count or 0))
    fail_count = max(0, int(fail_count or 0))
    cancelled_count = max(0, int(cancelled_count or 0))
    unfinished_count = max(0, int(unfinished_count or 0))

    if total_devices <= 0:
        return SCHEDULE_RUN_STATUS_SUCCEEDED
    if cancelled_count >= total_devices and success_count <= 0 and fail_count <= 0 and unfinished_count <= 0:
        return SCHEDULE_RUN_STATUS_CANCELLED
    if cancelled_count > 0 and unfinished_count <= 0:
        return SCHEDULE_RUN_STATUS_PARTIAL_CANCELLED
    if unfinished_count > 0:
        return SCHEDULE_RUN_STATUS_PARTIAL_FAILED if success_count > 0 else SCHEDULE_RUN_STATUS_FAILED
    if fail_count <= 0:
        return SCHEDULE_RUN_STATUS_SUCCEEDED
    if success_count <= 0:
        return SCHEDULE_RUN_STATUS_FAILED
    return SCHEDULE_RUN_STATUS_PARTIAL_FAILED
