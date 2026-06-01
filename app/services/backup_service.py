from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import json
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session
from sqlmodel import select

from app import crud
from app.models import BackupRecord, Device
from app.platforms import DEFAULT_COMMANDS, normalize_platform_id
from app.platforms import platforms_compatible
from app.core.time import format_local_datetime
from app.services import device_service, pagination_service, task_orchestration_service, task_state_service
from app.services.netmiko_client import run_netmiko_commands


from app.services.errors import ServiceError


@dataclass(slots=True)
class TriggerBackupResult:
    record_id: UUID
    device_id: int
    started_at: datetime
    template_id: int | None
    enqueued: bool


@dataclass(slots=True)
class TriggerBulkBackupResult:
    requested_ids: list[int]
    valid_ids: list[int]
    run_id: UUID | None
    jobs: list[tuple[int, UUID, int | None]]
    enqueued: bool
    enqueue_status: str
    enqueued_record_ids: list[UUID]


@dataclass(slots=True)
class BackupDetailResult:
    record: BackupRecord
    device: Device | None


@dataclass(slots=True)
class EnqueueScheduleRunResult:
    run_id: UUID
    jobs: list[tuple[int, UUID, int | None]]
    enqueued: bool
    enqueue_status: str
    enqueued_record_ids: list[UUID]


DEFAULT_NOISE_RULES = [
    r"^!.*last configuration",
    r"^ntp clock-period",
    r"^!.*NVRAM config last updated",
]
DEFAULT_DIFF_RULES = [
    {"scope": "global", "targets": [], "patterns": DEFAULT_NOISE_RULES},
]
DIFF_RULES_SETTING_KEY = "diff_ignore_rules"
NOISE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DEFAULT_NOISE_RULES]


def normalize_commands(commands_text: str) -> list[str]:
    commands: list[str] = []
    for line in (commands_text or "").splitlines():
        cmd = line.strip()
        if not cmd:
            continue
        commands.append(cmd)
    return commands


def backup_device(
    *,
    host: str,
    port: int,
    login_method: str,
    encoding: str,
    platform: str,
    username: str,
    password: str | None,
    enable_password: str | None,
    template_commands: str | None,
) -> str:
    commands_text = (template_commands or "").strip() or DEFAULT_COMMANDS.get(normalize_platform_id(platform), "")
    commands = normalize_commands(commands_text)
    if not commands:
        raise RuntimeError("No commands configured for this platform")
    return run_netmiko_commands(
        host=host,
        port=port,
        login_method=login_method,
        encoding=encoding,
        platform=platform,
        username=username,
        password=password,
        enable_password=enable_password,
        commands=commands,
    )


def get_backup_detail(session: Session, backup_id: UUID) -> BackupDetailResult:
    record = crud.get_backup(session, backup_id)
    if record is None:
        raise ServiceError(
            "Backup record not found",
            code="BACKUP_NOT_FOUND",
            status_code=404,
            context={"backup_id": str(backup_id)},
        )
    device = crud.get_device(session, record.device_id)
    return BackupDetailResult(record=record, device=device)


def _ensure_backup_access(
    detail: BackupDetailResult,
    *,
    allowed_group_ids: list[int] | None,
    backup_id: UUID,
) -> BackupDetailResult:
    if allowed_group_ids is None:
        return detail
    if detail.device is None:
        raise ServiceError(
            "Permission denied for this backup",
            code="BACKUP_DEVICE_FORBIDDEN",
            status_code=403,
            context={"backup_id": str(backup_id), "device_id": int(detail.record.device_id or 0)},
        )
    try:
        device_service.validate_device_access(
            detail.device,
            allowed_group_ids=allowed_group_ids,
            action="view",
        )
    except device_service.ServiceError as exc:
        raise ServiceError(
            exc.message,
            code="BACKUP_DEVICE_FORBIDDEN",
            status_code=exc.status_code,
            context={"backup_id": str(backup_id), "device_id": int(detail.device.id or 0)},
        ) from exc
    return detail


def _device_in_allowed_groups(device: Device | None, allowed_group_ids: list[int] | None) -> bool:
    if allowed_group_ids is None:
        return True
    if device is None:
        return False
    try:
        device_service.validate_device_access(
            device,
            allowed_group_ids=allowed_group_ids,
            action="view",
        )
    except device_service.ServiceError:
        return False
    return True


def get_backup_view_payload(
    session: Session,
    backup_id: UUID,
    *,
    offset_minutes: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    detail = _ensure_backup_access(
        get_backup_detail(session, backup_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=backup_id,
    )
    record = detail.record
    device = detail.device
    return {
        "device": _serialize_device(device, fallback_device_id=int(record.device_id)),
        "record": {
            "id": str(record.id),
            "device_id": int(record.device_id),
            "started_at": format_local_datetime(record.started_at, offset_minutes=offset_minutes),
            "finished_at": format_local_datetime(record.finished_at, offset_minutes=offset_minutes) if record.finished_at else None,
            "status": str(record.status or ""),
            "status_label": task_state_service.get_backup_record_status_label(record.status),
            "status_tone": task_state_service.get_backup_record_status_tone(record.status),
            "success": bool(record.success),
            "error_message": record.error_message or "",
            "config_text": record.config_text or "",
        },
    }


def get_backup_content(
    session: Session,
    backup_id: UUID,
    *,
    allowed_group_ids: list[int] | None = None,
) -> BackupDetailResult:
    return _ensure_backup_access(
        get_backup_detail(session, backup_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=backup_id,
    )


def list_task_backups(
    session: Session,
    *,
    wanted_ids: list[UUID] | None = None,
    limit: int = 20,
    offset_minutes: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    items: dict[str, tuple[datetime | None, dict[str, Any]]] = {}

    def _add(rows: list[tuple[BackupRecord, Device | None]]) -> None:
        for record, device in rows:
            if not _device_in_allowed_groups(device, allowed_group_ids):
                continue
            items[str(record.id)] = (
                record.started_at,
                {
                    "id": str(record.id),
                    "device": {
                        "id": int(device.id) if device and device.id else int(record.device_id),
                        "name": device.name if device else f"device-{record.device_id}",
                        "host": device.host if device else "",
                    },
                    "started_at": format_local_datetime(record.started_at, offset_minutes=offset_minutes),
                    "finished_at": format_local_datetime(record.finished_at, offset_minutes=offset_minutes) if record.finished_at else None,
                    "status": str(record.status or ""),
                    "status_label": task_state_service.get_backup_record_status_label(record.status),
                    "status_tone": task_state_service.get_backup_record_status_tone(record.status),
                    "success": bool(record.success),
                    "error_message": record.error_message or "",
                },
            )

    if wanted_ids:
        stmt = (
            select(BackupRecord, Device)
            .join(Device, Device.id == BackupRecord.device_id, isouter=True)
            .where(BackupRecord.id.in_(wanted_ids))
        )
        _add(session.exec(stmt).all())

    running_stmt = (
        select(BackupRecord, Device)
        .join(Device, Device.id == BackupRecord.device_id, isouter=True)
        .where(BackupRecord.status.in_(task_state_service.BACKUP_RECORD_ACTIVE_STATUSES))
        .order_by(BackupRecord.started_at.desc())
        .limit(100)
    )
    _add(session.exec(running_stmt).all())

    recent_stmt = (
        select(BackupRecord, Device)
        .join(Device, Device.id == BackupRecord.device_id, isouter=True)
        .where(BackupRecord.status.in_(task_state_service.BACKUP_RECORD_TERMINAL_STATUSES))
        .order_by(BackupRecord.started_at.desc())
        .limit(limit)
    )
    _add(session.exec(recent_stmt).all())

    ordered = [
        payload
        for _, payload in sorted(
            items.values(),
            key=lambda item: item[0] or datetime.min,
            reverse=True,
        )
    ]
    running_count = sum(
        1 for item in ordered if task_state_service.is_backup_record_active_status(item.get("status"))
    )
    return {"items": ordered, "running": running_count}


def list_device_backups_payload(
    session: Session,
    *,
    device_id: int,
    page: int = 1,
    limit: int = 10,
    offset_minutes: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError(
            "Device not found",
            code="BACKUP_DEVICE_NOT_FOUND",
            status_code=404,
            context={"device_id": device_id},
        )
    if allowed_group_ids is not None:
        device_service.validate_device_access(device, allowed_group_ids, action="view")

    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=True,
        default_limit=10,
        max_limit=200,
    )
    total = crud.count_device_backups(session, device_id)
    backups = crud.list_device_backups(session, device_id, limit=params.limit, offset=params.offset)
    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    return {
        "id": device.id,
        "name": device.name,
        "host": device.host,
        "platform": device.platform,
        "backups": [
            {
                "id": str(record.id),
                "started_at": format_local_datetime(record.started_at, offset_minutes=offset_minutes),
                "finished_at": format_local_datetime(record.finished_at, offset_minutes=offset_minutes) if record.finished_at else None,
                "status": str(record.status or ""),
                "status_label": task_state_service.get_backup_record_status_label(record.status),
                "status_tone": task_state_service.get_backup_record_status_tone(record.status),
                "success": record.success,
                "error_message": record.error_message,
                "config_snapshot_hash": record.config_snapshot_hash,
            }
            for record in backups
        ],
        "pagination": {
            "total": total,
            "page": pagination.page,
            "limit": pagination.limit,
            "total_pages": pagination.total_pages,
        },
    }


def list_backup_page_rows(
    session: Session,
    *,
    allowed_group_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    devices = [
        device
        for device in crud.list_devices(session)
        if device.id and _device_in_allowed_groups(device, allowed_group_ids)
    ]
    count_rows = session.exec(
        select(BackupRecord.device_id, func.count(BackupRecord.id)).group_by(BackupRecord.device_id)
    ).all()
    backup_counts = {int(device_id): int(cnt) for device_id, cnt in count_rows}
    return [
        {
            "id": int(device.id),
            "name": device.name,
            "host": device.host,
            "port": device.port,
            "platform": device.platform,
            "backup_count": backup_counts.get(int(device.id), 0),
        }
        for device in devices
    ]


def get_diff_rules_page_payload(session: Session) -> dict[str, Any]:
    rules = load_diff_rules(session)
    groups = [
        {"id": int(group.id), "name": group.name}
        for group in crud.list_groups(session)
        if group.id is not None
    ]
    return {
        "rules": rules,
        "default_rules": DEFAULT_DIFF_RULES,
        "groups": groups,
    }


def save_diff_rules(session: Session, payload: Any) -> list[dict[str, Any]]:
    normalized = normalize_diff_rules(payload)
    crud.set_setting(
        session,
        key=DIFF_RULES_SETTING_KEY,
        value=json.dumps(normalized, ensure_ascii=False),
    )
    return normalized


def get_diff_rules_required_permissions(
    session: Session,
    payload: Any,
) -> tuple[list[dict[str, Any]], set[str]]:
    current_rules = load_diff_rules(session)
    submitted_rules = normalize_diff_rules(payload)
    required: set[str] = set()

    if submitted_rules == current_rules:
        return submitted_rules, required

    current_serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in current_rules]
    submitted_serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in submitted_rules]

    def _is_subsequence(source: list[str], candidate: list[str]) -> bool:
        if len(candidate) > len(source):
            return False
        idx = 0
        for item in source:
            if idx < len(candidate) and item == candidate[idx]:
                idx += 1
        return idx == len(candidate)

    delete_only = len(submitted_serialized) < len(current_serialized) and _is_subsequence(current_serialized, submitted_serialized)
    if delete_only:
        required.add("diff_rules.delete")
        return submitted_rules, required

    current_counts = Counter(current_serialized)
    submitted_counts = Counter(submitted_serialized)

    if any(current_counts[key] > submitted_counts.get(key, 0) for key in current_counts):
        required.add("diff_rules.delete")
    required.add("diff_rules.update")
    return submitted_rules, required


def get_config_search_payload(
    session: Session,
    *,
    q: str = "",
    scope: str = "latest",
    page: int = 1,
    limit: int = 50,
    include_limit_param: bool = False,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=include_limit_param,
        default_limit=50,
        max_limit=500,
    )
    latest_only = scope == "latest"

    records = crud.search_config(
        session,
        q=q,
        latest_only=latest_only,
        limit=params.limit,
        offset=params.offset,
        allowed_group_ids=allowed_group_ids,
    )
    total = crud.count_config_search_results(
        session,
        q=q,
        latest_only=latest_only,
        allowed_group_ids=allowed_group_ids,
    )

    device_ids = sorted({int(record.device_id) for record in records if record.device_id is not None})
    device_map: dict[int, Device] = {}
    if device_ids:
        devices = session.exec(select(Device).where(Device.id.in_(device_ids))).all()
        device_map = {int(device.id): device for device in devices if device.id is not None}

    grouped_records: dict[int, list[BackupRecord]] = {}
    for record in records:
        if record.device_id is not None and int(record.device_id) in device_map:
            grouped_records.setdefault(int(record.device_id), []).append(record)

    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/config-search",
        params={"q": q, "scope": scope},
        limit=pagination.limit,
        default_limit=50,
        limit_explicit=params.limit_explicit,
    )

    return {
        "q": q,
        "scope": scope,
        "records": records,
        "total": total,
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
        "device_map": device_map,
        "grouped_records": grouped_records,
    }


def get_backup_download_payload(
    session: Session,
    backup_id: UUID,
    *,
    offset_minutes: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    detail = _ensure_backup_access(
        get_backup_detail(session, backup_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=backup_id,
    )
    record = detail.record
    device = detail.device
    device_id = record.device_id if record.device_id is not None else 0
    base_name = (device.name or "") if device else f"device-{device_id}"
    host = (device.host or "") if device else ""
    safe_host = "".join(ch for ch in str(host) if ch.isascii() and (ch.isalnum() or ch in ("-", "_", "."))).strip()

    if isinstance(record.started_at, datetime):
        timestamp = format_local_datetime(record.started_at, offset_minutes=offset_minutes).replace(":", "").replace(" ", "_")
    else:
        timestamp = "unknown_time"
    if not timestamp:
        timestamp = "unknown_time"

    suffix = f"_{safe_host}" if safe_host else ""
    filename_utf8 = f"{base_name}{suffix}_{timestamp}.txt"
    filename_ascii = "".join(
        ch for ch in str(filename_utf8) if ch.isascii() and (ch.isalnum() or ch in ("-", "_", "."))
    ).strip() or "backup.txt"
    content_disposition = f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{quote(filename_utf8)}"

    if isinstance(record.config_text, bytes):
        config_text = record.config_text.decode("utf-8", errors="replace")
    elif isinstance(record.config_text, str):
        config_text = record.config_text
    else:
        config_text = "" if record.config_text is None else str(record.config_text)

    return {
        "content_bytes": config_text.encode("utf-8", errors="replace"),
        "content_disposition": content_disposition,
    }


def trigger_backup(
    session: Session,
    *,
    device_id: int,
    template_id: int = 0,
    skip_email: bool = False,
    allowed_group_ids: list[int] | None = None,
) -> TriggerBackupResult:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError("Device not found", code="BACKUP_DEVICE_NOT_FOUND", status_code=404)
    if allowed_group_ids is not None:
        device_service.validate_device_access(device, allowed_group_ids, action="view")

    planned = task_orchestration_service.plan_single_backup(
        session,
        device_id=device_id,
        template_id=template_id,
    )
    enqueued = task_orchestration_service.enqueue_single_backup(
        session,
        planned=planned,
        skip_email=skip_email,
    )

    return TriggerBackupResult(
        record_id=planned.record_id,
        device_id=planned.device_id,
        started_at=planned.started_at,
        template_id=planned.template_id,
        enqueued=bool(enqueued),
    )


def trigger_bulk_backup(
    session: Session,
    *,
    requested_ids: list[int] | None = None,
    mode: str = "selected",
    allowed_group_ids: list[int] | None = None,
) -> TriggerBulkBackupResult:
    raw_ids = list(requested_ids or [])
    if mode == "all":
        raw_ids = [int(device.id) for device in crud.list_devices(session) if device.id]

    if not raw_ids:
        return TriggerBulkBackupResult(
            requested_ids=[],
            valid_ids=[],
            run_id=None,
            jobs=[],
            enqueued=False,
            enqueue_status="none",
            enqueued_record_ids=[],
        )

    valid_ids: list[int] = []
    for device in crud.get_devices_subset(session, raw_ids):
        if not device.id:
            continue
        try:
            device_service.validate_device_access(device, allowed_group_ids, action="view")
        except device_service.ServiceError:
            continue
        valid_ids.append(int(device.id))
    if not valid_ids:
        return TriggerBulkBackupResult(
            requested_ids=raw_ids,
            valid_ids=[],
            run_id=None,
            jobs=[],
            enqueued=False,
            enqueue_status="none",
            enqueued_record_ids=[],
        )

    run_id, jobs = task_orchestration_service.plan_device_batch_run(
        session,
        device_ids=valid_ids,
        trigger="manual",
        schedule_id=0,
    )
    enqueue_result = enqueue_schedule_jobs(session, run_id=run_id, jobs=jobs)
    return TriggerBulkBackupResult(
        requested_ids=raw_ids,
        valid_ids=valid_ids,
        run_id=run_id,
        jobs=jobs,
        enqueued=enqueue_result.enqueued,
        enqueue_status=enqueue_result.enqueue_status,
        enqueued_record_ids=enqueue_result.enqueued_record_ids,
    )


def delete_backup(
    session: Session,
    backup_id: UUID,
    *,
    allowed_group_ids: list[int] | None = None,
) -> BackupDetailResult:
    detail = _ensure_backup_access(
        get_backup_detail(session, backup_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=backup_id,
    )
    if task_state_service.is_backup_record_active_status(detail.record.status):
        raise ServiceError(
            "Active backup task cannot be deleted",
            code="BACKUP_DELETE_ACTIVE_RECORD",
            status_code=409,
            context={"backup_id": str(backup_id), "device_id": int(detail.record.device_id or 0)},
        )
    deleted = crud.bulk_delete_backups(session, [backup_id])
    if deleted <= 0:
        raise ServiceError(
            "Backup record not found",
            code="BACKUP_NOT_FOUND",
            status_code=404,
            context={"backup_id": str(backup_id)},
        )
    return detail


def enqueue_schedule_jobs(
    session: Session,
    *,
    run_id: UUID,
    jobs: list[tuple[int, UUID, int | None]],
) -> EnqueueScheduleRunResult:
    enqueue_status, enqueued_record_ids = task_orchestration_service.enqueue_schedule_run(
        session,
        run_id=run_id,
        jobs=jobs,
        skip_email=True,
    )

    return EnqueueScheduleRunResult(
        run_id=run_id,
        jobs=jobs,
        enqueued=enqueue_status != "none",
        enqueue_status=enqueue_status,
        enqueued_record_ids=enqueued_record_ids,
    )


def normalize_diff_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    def _normalize_rule_targets(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [v.strip() for v in raw.split(",") if v.strip()]
        if isinstance(raw, (list, tuple, set)):
            out: list[str] = []
            for item in raw:
                v = str(item).strip()
                if v:
                    out.append(v)
            return out
        return []

    def _normalize_rule_patterns(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [v.strip() for v in raw.splitlines() if v.strip()]
        if isinstance(raw, (list, tuple, set)):
            out: list[str] = []
            for item in raw:
                v = str(item).strip()
                if v:
                    out.append(v)
            return out
        return []

    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "global").strip().lower()
        if scope not in {"global", "platform", "group"}:
            continue
        targets = _normalize_rule_targets(item.get("targets"))
        patterns = _normalize_rule_patterns(item.get("patterns"))
        if scope != "global" and not targets:
            continue
        if not patterns:
            continue
        if scope == "global":
            targets = []
        out.append({"scope": scope, "targets": targets, "patterns": patterns})
    return out


def load_diff_rules(session: Session) -> list[dict[str, Any]]:
    raw = crud.get_setting(session, key=DIFF_RULES_SETTING_KEY)
    if not raw:
        return DEFAULT_DIFF_RULES
    try:
        data = json.loads(raw)
    except Exception:
        return DEFAULT_DIFF_RULES
    normalized = normalize_diff_rules(data)
    return normalized or DEFAULT_DIFF_RULES


def has_meaningful_config_change(
    session: Session,
    *,
    current_text: str | None,
    previous_text: str | None,
    current_device: Device | None = None,
    previous_device: Device | None = None,
) -> bool:
    summary = summarize_meaningful_config_change(
        session,
        current_text=current_text,
        previous_text=previous_text,
        current_device=current_device,
        previous_device=previous_device,
    )
    return bool(summary.get("changed"))


def summarize_meaningful_config_change(
    session: Session,
    *,
    current_text: str | None,
    previous_text: str | None,
    current_device: Device | None = None,
    previous_device: Device | None = None,
    sample_limit: int | None = None,
    context_lines: int = 2,
) -> dict[str, Any]:
    diff_rules = load_diff_rules(session)
    noise_patterns = _build_noise_patterns(diff_rules, current_device, previous_device)
    current_lines = _normalize_lines(
        current_text,
        ignore_noise_lines=True,
        noise_patterns=noise_patterns,
    )
    previous_lines = _normalize_lines(
        previous_text,
        ignore_noise_lines=True,
        noise_patterns=noise_patterns,
    )
    changed = current_lines != previous_lines
    samples: list[dict[str, str | int]] = []
    added_count = 0
    deleted_count = 0
    total_sample_rows = 0

    if changed:
        diff_lines = _build_unified_diff_payload(
            current_lines,
            previous_lines,
            only_changed_lines=True,
            context_lines=max(0, int(context_lines or 0)),
        )
        limit = max(1, int(sample_limit)) if sample_limit is not None else len(diff_lines) or 1
        for row in diff_lines:
            row_type = row.get("type")
            if row_type == "add":
                added_count += 1
                total_sample_rows += 1
                if len(samples) < limit:
                    samples.append({"kind": "add", "prefix": "+", "text": str(row.get("text") or "")})
            elif row_type == "del":
                deleted_count += 1
                total_sample_rows += 1
                if len(samples) < limit:
                    samples.append({"kind": "del", "prefix": "-", "text": str(row.get("text") or "")})
            elif row_type == "context":
                total_sample_rows += 1
                if len(samples) < limit:
                    samples.append({"kind": "context", "prefix": " ", "text": str(row.get("text") or "")})
            elif row_type == "skip":
                total_sample_rows += 1
                if len(samples) < limit:
                    samples.append({"kind": "skip", "prefix": "...", "text": f"省略 {int(row.get('count') or 0)} 行"})

    return {
        "changed": changed,
        "added_count": added_count,
        "deleted_count": deleted_count,
        "sample_lines": samples,
        "sample_limit": limit if changed else (max(1, int(sample_limit)) if sample_limit is not None else 0),
        "total_diff_lines": added_count + deleted_count,
        "context_lines": max(0, int(context_lines or 0)),
        "total_sample_rows": total_sample_rows,
    }


def build_backup_diff(
    session: Session,
    *,
    backup_id: UUID,
    other_id: UUID,
    mode: str = "unified",
    only_changed_lines: bool = True,
    ignore_noise_lines: bool = False,
    context_lines: int = 2,
    offset_minutes: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    mode = (mode or "unified").strip().lower()
    if mode not in {"unified", "split"}:
        mode = "unified"
    detail_a = _ensure_backup_access(
        get_backup_detail(session, backup_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=backup_id,
    )
    detail_b = _ensure_backup_access(
        get_backup_detail(session, other_id),
        allowed_group_ids=allowed_group_ids,
        backup_id=other_id,
    )
    a = detail_a.record
    b = detail_b.record
    device_a = detail_a.device
    device_b = detail_b.device
    diff_rules = load_diff_rules(session)

    if a.started_at and b.started_at and a.started_at < b.started_at:
        a, b = b, a
        device_a, device_b = device_b, device_a

    noise_patterns = _build_noise_patterns(diff_rules, device_a, device_b) if ignore_noise_lines else None
    a_lines = _normalize_lines(
        a.config_text,
        ignore_noise_lines=ignore_noise_lines,
        noise_patterns=noise_patterns,
    )
    b_lines = _normalize_lines(
        b.config_text,
        ignore_noise_lines=ignore_noise_lines,
        noise_patterns=noise_patterns,
    )

    payload: dict[str, Any] = {
        "mode": mode,
        "only_changed_lines": bool(only_changed_lines),
        "ignore_noise_lines": bool(ignore_noise_lines),
        "device_a": _serialize_device(device_a, fallback_device_id=int(a.device_id)),
        "device_b": _serialize_device(device_b, fallback_device_id=int(b.device_id)),
        "device": _serialize_device(device_a, fallback_device_id=int(a.device_id)),
        "a": _serialize_backup_record(a, offset_minutes=offset_minutes),
        "b": _serialize_backup_record(b, offset_minutes=offset_minutes),
    }

    if mode == "split":
        payload["rows"] = _build_split_diff_payload(
            a_lines,
            b_lines,
            only_changed_lines=bool(only_changed_lines),
            context_lines=context_lines,
        )
    else:
        payload["lines"] = _build_unified_diff_payload(
            a_lines,
            b_lines,
            only_changed_lines=bool(only_changed_lines),
            context_lines=context_lines,
        )
    return payload


def _serialize_device(device: Device | None, *, fallback_device_id: int) -> dict[str, Any]:
    return {
        "id": int(device.id or 0) if device else int(fallback_device_id),
        "name": device.name if device else f"device-{fallback_device_id}",
        "host": device.host if device else "",
        "port": device.port if device else 0,
        "platform": device.platform if device else "",
    }


def _serialize_backup_record(record: BackupRecord, *, offset_minutes: int) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "started_at": format_local_datetime(record.started_at, offset_minutes=offset_minutes),
        "finished_at": format_local_datetime(record.finished_at, offset_minutes=offset_minutes) if record.finished_at else None,
        "success": bool(record.success),
    }


def _collect_rule_patterns(rules: list[dict[str, Any]], device: Device | None) -> list[str]:
    patterns: list[str] = []
    device_platform = normalize_platform_id(device.platform) if device else ""
    device_group = str(device.group_id) if device and device.group_id else ""
    for rule in rules:
        scope = rule.get("scope")
        if scope == "global":
            patterns.extend(rule.get("patterns") or [])
            continue
        targets = rule.get("targets") or []
        if scope == "platform":
            target_set = {normalize_platform_id(t) for t in targets}
            if device_platform and device_platform in target_set:
                patterns.extend(rule.get("patterns") or [])
        elif scope == "group":
            if device_group and device_group in {str(t) for t in targets}:
                patterns.extend(rule.get("patterns") or [])
    return patterns


def _compile_noise_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for raw in patterns:
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return out


def _build_noise_patterns(
    rules: list[dict[str, Any]],
    device_a: Device | None,
    device_b: Device | None,
) -> list[re.Pattern[str]]:
    combined: list[str] = []
    combined.extend(_collect_rule_patterns(rules, device_a))
    combined.extend(_collect_rule_patterns(rules, device_b))
    seen = set()
    uniq: list[str] = []
    for pattern in combined:
        if pattern in seen:
            continue
        seen.add(pattern)
        uniq.append(pattern)
    return _compile_noise_patterns(uniq)


def _normalize_lines(
    text: str | None,
    *,
    ignore_noise_lines: bool = False,
    noise_patterns: list[re.Pattern[str]] | None = None,
) -> list[str]:
    lines = (text or "").splitlines()
    if ignore_noise_lines:
        patterns = noise_patterns if noise_patterns is not None else NOISE_PATTERNS
        if not patterns:
            return lines
        filtered: list[str] = []
        for line in lines:
            stripped = line.strip()
            is_noise = False
            for pattern in patterns:
                if pattern.search(stripped):
                    is_noise = True
                    break
            if not is_noise:
                filtered.append(line)
        return filtered
    return lines


def _build_unified_diff_payload(
    a_lines: list[str],
    b_lines: list[str],
    *,
    only_changed_lines: bool,
    context_lines: int = 2,
) -> list[dict[str, Any]]:
    sm = SequenceMatcher(a=a_lines, b=b_lines)
    full: list[dict[str, Any]] = []
    a_ln = 1
    b_ln = 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            count = i2 - i1
            for k in range(count):
                full.append({"type": "context", "a_lineno": a_ln, "b_lineno": b_ln, "text": a_lines[i1 + k]})
                a_ln += 1
                b_ln += 1
            continue

        if tag in {"delete", "replace"}:
            for k in range(i2 - i1):
                full.append({"type": "add", "a_lineno": a_ln, "b_lineno": None, "text": a_lines[i1 + k]})
                a_ln += 1
        if tag in {"insert", "replace"}:
            for k in range(j2 - j1):
                full.append({"type": "del", "a_lineno": None, "b_lineno": b_ln, "text": b_lines[j1 + k]})
                b_ln += 1

    if not only_changed_lines:
        return full

    context = max(0, min(int(context_lines or 0), 20))
    keep = [False] * len(full)
    for idx, row in enumerate(full):
        if row.get("type") in {"add", "del"}:
            lo = max(0, idx - context)
            hi = min(len(full) - 1, idx + context)
            for j in range(lo, hi + 1):
                keep[j] = True

    out: list[dict[str, Any]] = []
    skipped = 0
    for idx, row in enumerate(full):
        if keep[idx]:
            if skipped:
                out.append({"type": "skip", "count": skipped})
                skipped = 0
            out.append(row)
            continue
        if row.get("type") == "context":
            skipped += 1
    if skipped:
        out.append({"type": "skip", "count": skipped})
    return out


def _build_split_diff_payload(
    a_lines: list[str],
    b_lines: list[str],
    *,
    only_changed_lines: bool,
    context_lines: int = 2,
) -> list[dict[str, Any]]:
    sm = SequenceMatcher(a=a_lines, b=b_lines)
    full: list[dict[str, Any]] = []
    a_ln = 1
    b_ln = 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            count = i2 - i1
            for k in range(count):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": a_ln, "text": a_lines[i1 + k], "kind": "context"},
                        "b": {"lineno": b_ln, "text": b_lines[j1 + k], "kind": "context"},
                    }
                )
                a_ln += 1
                b_ln += 1
            continue

        if tag == "delete":
            for k in range(i2 - i1):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": a_ln, "text": a_lines[i1 + k], "kind": "add"},
                        "b": {"lineno": None, "text": "", "kind": "empty"},
                    }
                )
                a_ln += 1
            continue

        if tag == "insert":
            for k in range(j2 - j1):
                full.append(
                    {
                        "type": "row",
                        "a": {"lineno": None, "text": "", "kind": "empty"},
                        "b": {"lineno": b_ln, "text": b_lines[j1 + k], "kind": "del"},
                    }
                )
                b_ln += 1
            continue

        a_chunk = a_lines[i1:i2]
        b_chunk = b_lines[j1:j2]
        rows = max(len(a_chunk), len(b_chunk))
        for idx in range(rows):
            has_a = idx < len(a_chunk)
            has_b = idx < len(b_chunk)
            a_text = a_chunk[idx] if has_a else ""
            b_text = b_chunk[idx] if has_b else ""

            if has_a and has_b:
                a_info = {"lineno": a_ln, "text": a_text, "kind": "chg"}
                b_info = {"lineno": b_ln, "text": b_text, "kind": "chg"}
            elif has_a:
                a_info = {"lineno": a_ln, "text": a_text, "kind": "add"}
                b_info = {"lineno": None, "text": "", "kind": "empty"}
            else:
                a_info = {"lineno": None, "text": "", "kind": "empty"}
                b_info = {"lineno": b_ln, "text": b_text, "kind": "del"}

            full.append({"type": "row", "a": a_info, "b": b_info})
            if has_a:
                a_ln += 1
            if has_b:
                b_ln += 1

    if not only_changed_lines:
        return full

    context = max(0, min(int(context_lines or 0), 20))
    keep = [False] * len(full)
    for idx, row in enumerate(full):
        if row.get("type") != "row":
            continue
        a_kind = (row.get("a") or {}).get("kind")
        b_kind = (row.get("b") or {}).get("kind")
        if a_kind in {"add", "del", "chg"} or b_kind in {"add", "del", "chg"}:
            lo = max(0, idx - context)
            hi = min(len(full) - 1, idx + context)
            for j in range(lo, hi + 1):
                keep[j] = True

    out: list[dict[str, Any]] = []
    skipped = 0
    for idx, row in enumerate(full):
        if keep[idx]:
            if skipped:
                out.append({"type": "skip", "count": skipped})
                skipped = 0
            out.append(row)
            continue
        if row.get("type") == "row":
            a_kind = (row.get("a") or {}).get("kind")
            b_kind = (row.get("b") or {}).get("kind")
            if a_kind == "context" and b_kind == "context":
                skipped += 1
    if skipped:
        out.append({"type": "skip", "count": skipped})
    return out
