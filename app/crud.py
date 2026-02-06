from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Any
from uuid import UUID

from sqlalchemy import func, or_, desc
from sqlmodel import Session, select, delete

from app.models import (
    AppSetting,
    AuditLog,
    BackupRecord,
    BackupSchedule,
    BackupScheduleRun,
    BackupScheduleRunItem,
    BackupTemplate,
    Credential,
    Device,
    DeviceGroup,
    LoginLog,
    User,
)
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.auth import hash_password, verify_password


def list_devices(session: Session) -> list[Device]:
    return list(session.exec(select(Device).order_by(Device.id)))


def search_devices(
    session: Session,
    *,
    q: str | None,
    login_method: str | None = None,
    platform: str | None,
    group_id: int | None,
    reachability_status: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Device]:
    stmt = select(Device)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Device.name.like(like), Device.host.like(like)))
    if login_method:
        lm = (login_method or "").strip().lower()
        if lm in {"ssh", "telnet"}:
            stmt = stmt.where(Device.login_method == lm)
    if platform:
        from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id

        base = normalize_platform_id(platform)
        candidates = {base}
        telnet = TELNET_DEVICE_TYPE_MAP.get(base)
        if telnet:
            candidates.add(telnet)
        else:
            candidates.add(base + "_telnet")
        stmt = stmt.where(Device.platform.in_(list(candidates)))
    if group_id:
        stmt = stmt.where(Device.group_id == group_id)
    if reachability_status is not None:
        stmt = stmt.where(Device.reachability_status == reachability_status)
    stmt = stmt.order_by(Device.id).offset(offset).limit(limit)
    return list(session.exec(stmt))


def count_devices(
    session: Session,
    *,
    q: str | None,
    login_method: str | None = None,
    platform: str | None,
    group_id: int | None,
    reachability_status: bool | None = None,
) -> int:
    stmt = select(func.count()).select_from(Device)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Device.name.like(like), Device.host.like(like)))
    if login_method:
        lm = (login_method or "").strip().lower()
        if lm in {"ssh", "telnet"}:
            stmt = stmt.where(Device.login_method == lm)
    if platform:
        from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id

        base = normalize_platform_id(platform)
        candidates = {base}
        telnet = TELNET_DEVICE_TYPE_MAP.get(base)
        if telnet:
            candidates.add(telnet)
        else:
            candidates.add(base + "_telnet")
        stmt = stmt.where(Device.platform.in_(list(candidates)))
    if group_id:
        stmt = stmt.where(Device.group_id == group_id)
    if reachability_status is not None:
        stmt = stmt.where(Device.reachability_status == reachability_status)
    return int(session.exec(stmt).one())


def get_device(session: Session, device_id: int) -> Device | None:
    return session.get(Device, device_id)


def get_devices_subset(session: Session, device_ids: list[int]) -> list[Device]:
    if not device_ids:
        return []
    stmt = select(Device).where(Device.id.in_(device_ids))
    return list(session.exec(stmt))


def create_device(session: Session, *, device: Device) -> Device:
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def delete_device(session: Session, device_id: int, commit: bool = True) -> None:
    device = session.get(Device, device_id)
    if device is None:
        return
    
    # 1. 删除关联的备份记录 (BackupRecord)
    stmt_backups = delete(BackupRecord).where(BackupRecord.device_id == device_id)
    session.exec(stmt_backups)
    
    # 2. 删除关联的计划运行明细 (BackupScheduleRunItem)
    stmt_run_items = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.device_id == device_id)
    session.exec(stmt_run_items)
    
    # 3. 删除设备本身
    session.delete(device)
    
    # 4. 从所有计划任务的目标列表中移除该设备 (device:ID 或 纯数字 ID)
    schedules = session.exec(select(BackupSchedule)).all()
    target_token = f"device:{device_id}"
    str_id = str(device_id)
    for s in schedules:
        if not s.targets or s.targets == "all":
            continue
        
        # 使用与前端和调度器一致的 split() 逻辑，支持空格和换行
        tokens = s.targets.split()
        if target_token in tokens or str_id in tokens:
            new_tokens = [t for t in tokens if t != target_token and t != str_id]
            # 如果清理后为空，建议设为一个特殊的占位符或保持为空
            # 注意：系统逻辑中，targets 为空可能被视为“全部设备”，
            # 但在设备删除场景下，保持为空字符串是相对合理的。
            s.targets = "\n".join(new_tokens)
            session.add(s)

    if commit:
        session.commit()


def update_device(
    session: Session,
    device_id: int,
    *,
    name: str,
    host: str,
    port: int,
    login_method: str,
    platform: str,
    group_id: int | None,
    credential_id: int | None,
    default_template_id: int | None,
) -> Device | None:
    device = session.get(Device, device_id)
    if device is None:
        return None
    device.name = name.strip()
    device.host = host.strip()
    device.port = int(port)
    device.login_method = (login_method or "ssh").strip().lower()
    device.platform = platform
    device.group_id = group_id
    device.credential_id = credential_id
    device.default_template_id = default_template_id
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def count_groups(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(DeviceGroup)).one())


def list_groups(session: Session, *, limit: int | None = None, offset: int = 0) -> list[DeviceGroup]:
    stmt = select(DeviceGroup).order_by(DeviceGroup.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def group_usage_count(session: Session, group_id: int) -> int:
    stmt = select(func.count()).where(Device.group_id == group_id)
    return int(session.exec(stmt).one())


def create_group(session: Session, *, name: str) -> DeviceGroup:
    group = DeviceGroup(name=name.strip())
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def delete_group(session: Session, group_id: int) -> None:
    group = session.get(DeviceGroup, group_id)
    if group is None:
        return
    session.delete(group)
    session.commit()


def update_group(session: Session, group_id: int, *, name: str) -> DeviceGroup | None:
    group = session.get(DeviceGroup, group_id)
    if group is None:
        return None
    group.name = name.strip()
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def count_credentials(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(Credential)).one())


def list_credentials(session: Session, *, limit: int | None = None, offset: int = 0) -> list[Credential]:
    stmt = select(Credential).order_by(Credential.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def get_credential(session: Session, credential_id: int) -> Credential | None:
    return session.get(Credential, credential_id)


def create_credential(session: Session, *, credential: Credential) -> Credential:
    # encryption is handled by Credential model property setters
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential


def update_credential(
    session: Session,
    credential_id: int,
    *,
    name: str,
    username: str,
    password: str | None,
    enable_password: str | None,
    remarks: str | None = None,
) -> Credential | None:
    credential = session.get(Credential, credential_id)
    if credential is None:
        return None
    credential.name = name.strip()
    credential.username = username.strip()
    if password is not None:
        credential.password = encrypt_secret(password)
    if enable_password is not None:
        credential.enable_password = encrypt_secret(enable_password)
    credential.remarks = remarks
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential


def credential_usage_count(session: Session, credential_id: int) -> int:
    stmt = select(func.count()).where(Device.credential_id == credential_id)
    return int(session.exec(stmt).one())


def delete_credential(session: Session, credential_id: int) -> None:
    in_use = session.exec(select(Device).where(Device.credential_id == credential_id).limit(1)).first()
    if in_use is not None:
        raise RuntimeError("Credential is in use by devices")
    credential = session.get(Credential, credential_id)
    if credential is None:
        return
    session.delete(credential)
    session.commit()


def get_credential_secrets(credential: Credential) -> dict[str, str | None]:
    return {
        "username": credential.username,
        "password": credential.password,
        "enable_password": credential.enable_password,
    }


def count_templates(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(BackupTemplate)).one())


def list_templates(session: Session, *, limit: int | None = None, offset: int = 0) -> list[BackupTemplate]:
    stmt = select(BackupTemplate).order_by(BackupTemplate.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def get_template(session: Session, template_id: int) -> BackupTemplate | None:
    return session.get(BackupTemplate, template_id)


def create_template(session: Session, *, template: BackupTemplate) -> BackupTemplate:
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def delete_template(session: Session, template_id: int) -> None:
    template = session.get(BackupTemplate, template_id)
    if template is None:
        return
    session.delete(template)
    session.commit()


def update_template(session: Session, template_id: int, *, name: str, platform: str, commands: str) -> BackupTemplate | None:
    template = session.get(BackupTemplate, template_id)
    if template is None:
        return None
    template.name = name
    template.platform = platform
    template.commands = commands
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def create_backup_record(
    session: Session,
    *,
    device_id: int,
    template_id: int | None,
) -> BackupRecord:
    record = BackupRecord(device_id=device_id, template_id=template_id, success=False)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def finish_backup_record(
    session: Session,
    *,
    record_id: UUID,
    success: bool,
    config_text: str | None,
    error_message: str | None,
    duration_seconds: float | None = None,
    failure_type: str | None = None,
) -> BackupRecord | None:
    record = session.get(BackupRecord, record_id)
    if record is None:
        return None
    record.finished_at = datetime.utcnow()
    record.success = success
    record.config_text = config_text
    record.error_message = error_message
    record.duration_seconds = duration_seconds
    record.failure_type = failure_type
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_backups(session: Session, *, limit: int = 50) -> list[BackupRecord]:
    stmt = select(BackupRecord).order_by(BackupRecord.started_at.desc()).limit(limit)
    return list(session.exec(stmt))


def list_device_backups(session: Session, device_id: int, *, limit: int = 50, offset: int = 0) -> list[BackupRecord]:
    stmt = (
        select(BackupRecord)
        .where(BackupRecord.device_id == device_id)
        .order_by(BackupRecord.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(stmt))


def count_device_backups(session: Session, device_id: int) -> int:
    stmt = select(func.count()).select_from(BackupRecord).where(BackupRecord.device_id == device_id)
    return int(session.exec(stmt).one())


def get_backup(session: Session, backup_id: UUID) -> BackupRecord | None:
    return session.get(BackupRecord, backup_id)


def list_backups_by_ids(session: Session, backup_ids: Iterable[UUID]) -> list[BackupRecord]:
    ids = list(backup_ids)
    if not ids:
        return []
    stmt = select(BackupRecord).where(BackupRecord.id.in_(ids)).order_by(BackupRecord.started_at.desc())
    return list(session.exec(stmt))


def search_config(
    session: Session,
    *,
    q: str,
    latest_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[BackupRecord]:
    # Join with Device to ensure we only search for existing devices
    stmt = select(BackupRecord).join(Device, BackupRecord.device_id == Device.id).where(BackupRecord.success == True)

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(BackupRecord.config_text.like(like))

    if latest_only:
        # Subquery to get the latest successful backup for each device
        subq = (
            select(
                BackupRecord.device_id,
                func.max(BackupRecord.started_at).label("max_started"),
            )
            .where(BackupRecord.success == True)
            .group_by(BackupRecord.device_id)
            .subquery()
        )
        stmt = stmt.join(
            subq,
            (BackupRecord.device_id == subq.c.device_id)
            & (BackupRecord.started_at == subq.c.max_started),
        )

    stmt = stmt.order_by(desc(BackupRecord.started_at)).offset(offset).limit(limit)
    return list(session.exec(stmt))


def count_config_search_results(
    session: Session,
    *,
    q: str,
    latest_only: bool = True,
) -> int:
    # Join with Device to ensure we only count results for existing devices
    stmt = (
        select(func.count())
        .select_from(BackupRecord)
        .join(Device, BackupRecord.device_id == Device.id)
        .where(BackupRecord.success == True)
    )

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(BackupRecord.config_text.like(like))

    if latest_only:
        subq = (
            select(
                BackupRecord.device_id,
                func.max(BackupRecord.started_at).label("max_started"),
            )
            .where(BackupRecord.success == True)
            .group_by(BackupRecord.device_id)
            .subquery()
        )
        stmt = stmt.join(
            subq,
            (BackupRecord.device_id == subq.c.device_id)
            & (BackupRecord.started_at == subq.c.max_started),
        )

    return int(session.exec(stmt).one())


def get_device_secrets(session: Session, device: Device) -> dict[str, str | None]:
    if not device.credential_id:
        return {"username": None, "password": None, "enable_password": None, "ssh_key_path": None}
    credential = session.get(Credential, device.credential_id)
    if not credential:
        return {"username": None, "password": None, "enable_password": None, "ssh_key_path": None}
    return get_credential_secrets(credential)


def set_setting(session: Session, *, key: str, value: str) -> AppSetting:
    item = session.get(AppSetting, key)
    if item is None:
        item = AppSetting(key=key, value=value)
    else:
        item.value = value
        item.updated_at = datetime.utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def get_setting(session: Session, *, key: str) -> str | None:
    item = session.get(AppSetting, key)
    if item is None:
        return None
    return item.value


def count_schedules(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(BackupSchedule)).one())


def list_schedules(session: Session, *, limit: int | None = None, offset: int = 0) -> list[BackupSchedule]:
    stmt = select(BackupSchedule).order_by(BackupSchedule.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def get_schedule(session: Session, schedule_id: int) -> BackupSchedule | None:
    return session.get(BackupSchedule, schedule_id)


def create_schedule(session: Session, *, schedule: BackupSchedule) -> BackupSchedule:
    schedule.updated_at = datetime.utcnow()
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def update_schedule(
    session: Session,
    schedule_id: int,
    *,
    name: str | None = None,
    crontab: str | None = None,
    enabled: bool | None = None,
    targets: str | None = None,
) -> BackupSchedule | None:
    item = session.get(BackupSchedule, schedule_id)
    if item is None:
        return None
    if name is not None:
        item.name = name.strip()
    if crontab is not None:
        item.crontab = crontab.strip()
    if enabled is not None:
        item.enabled = bool(enabled)
    if targets is not None:
        item.targets = targets or ""
    item.updated_at = datetime.utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def delete_schedule(session: Session, schedule_id: int) -> None:
    item = session.get(BackupSchedule, schedule_id)
    if item is None:
        return
    run_items = session.exec(select(BackupScheduleRunItem).where(BackupScheduleRunItem.schedule_id == schedule_id)).all()
    for it in run_items:
        session.delete(it)
    runs = session.exec(select(BackupScheduleRun).where(BackupScheduleRun.schedule_id == schedule_id)).all()
    for r in runs:
        session.delete(r)
    session.delete(item)
    session.commit()


def create_schedule_run(
    session: Session,
    *,
    schedule_id: int,
    trigger: str,
    total_devices: int,
) -> BackupScheduleRun:
    run = BackupScheduleRun(
        schedule_id=int(schedule_id),
        trigger=(trigger or "manual").strip() or "manual",
        total_devices=int(total_devices or 0),
        success_count=0,
        fail_count=0,
        error_message=None,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def get_schedule_run(session: Session, run_id: UUID) -> BackupScheduleRun | None:
    return session.get(BackupScheduleRun, run_id)


def finish_schedule_run(
    session: Session,
    *,
    run_id: UUID,
    success_count: int,
    fail_count: int,
    error_message: str | None,
) -> BackupScheduleRun | None:
    run = session.get(BackupScheduleRun, run_id)
    if run is None:
        return None
    run.finished_at = datetime.utcnow()
    run.success_count = int(success_count or 0)
    run.fail_count = int(fail_count or 0)
    run.error_message = (error_message or "").strip() or None
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def list_schedule_runs(session: Session, schedule_id: int, *, limit: int = 50) -> list[BackupScheduleRun]:
    stmt = (
        select(BackupScheduleRun)
        .where(BackupScheduleRun.schedule_id == int(schedule_id))
        .order_by(BackupScheduleRun.started_at.desc())
        .limit(limit)
    )
    return list(session.exec(stmt))


def list_latest_schedule_runs(session: Session, schedule_ids: Iterable[int]) -> dict[int, BackupScheduleRun]:
    ids = [int(x) for x in schedule_ids]
    if not ids:
        return {}
    stmt = select(BackupScheduleRun).where(BackupScheduleRun.schedule_id.in_(ids)).order_by(BackupScheduleRun.started_at.desc())
    latest: dict[int, BackupScheduleRun] = {}
    for r in session.exec(stmt):
        sid = int(r.schedule_id)
        if sid not in latest:
            latest[sid] = r
    return latest


def add_schedule_run_item(
    session: Session,
    *,
    run_id: UUID,
    schedule_id: int,
    backup_id: UUID,
    device_id: int,
) -> BackupScheduleRunItem:
    item = BackupScheduleRunItem(
        run_id=run_id,
        schedule_id=int(schedule_id),
        backup_id=backup_id,
        device_id=int(device_id),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def list_schedule_run_items(session: Session, run_id: UUID) -> list[BackupScheduleRunItem]:
    stmt = select(BackupScheduleRunItem).where(BackupScheduleRunItem.run_id == run_id).order_by(BackupScheduleRunItem.id)
    return list(session.exec(stmt))


def bulk_delete_backups(session: Session, backup_ids: Iterable[UUID]) -> int:
    count = 0
    for bid in backup_ids:
        record = session.get(BackupRecord, bid)
        if record is None:
            continue
        # 同时删除关联的 run items
        stmt = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.backup_id == bid)
        session.exec(stmt)
        session.delete(record)
        count += 1
    session.commit()
    return count


def cleanup_old_backups(session: Session, days: int) -> int:
    """清理指定天数之前的备份记录和运行日志"""
    if days <= 0:
        return 0
    
    threshold = datetime.utcnow() - timedelta(days=days)
    
    # 1. 查找过期的备份记录
    stmt_records = select(BackupRecord.id, BackupRecord.device_id, BackupRecord.started_at, BackupRecord.success).where(BackupRecord.started_at < threshold)
    records_to_check = session.exec(stmt_records).all()
    
    if not records_to_check:
        return 0

    # 兜底保护：确保每台设备至少保留一份最近的成功备份
    # 策略：
    # 1. 找出所有涉及的 device_id
    # 2. 对每个 device，查询其最近一次成功的备份 ID
    # 3. 如果该 ID 在待删除列表中，将其移除

    device_ids = {r.device_id for r in records_to_check}
    latest_success_map = {} # device_id -> backup_id

    for did in device_ids:
        # 查询该设备最近一次成功的备份
        stmt_latest = (
            select(BackupRecord.id)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.success == True)
            .order_by(BackupRecord.started_at.desc())
            .limit(1)
        )
        latest_id = session.exec(stmt_latest).first()
        if latest_id:
            latest_success_map[did] = latest_id

    record_ids_to_delete = []
    for r in records_to_check:
        # 如果是该设备最新的成功备份，则跳过删除
        if r.success and latest_success_map.get(r.device_id) == r.id:
            continue
        record_ids_to_delete.append(r.id)
    
    if record_ids_to_delete:
        # 2. 删除关联的 run items
        stmt_del_items = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.backup_id.in_(record_ids_to_delete))
        session.exec(stmt_del_items)
        
        # 3. 删除过期的备份记录
        stmt_del_records = delete(BackupRecord).where(BackupRecord.id.in_(record_ids_to_delete))
        session.exec(stmt_del_records)
    
    # 4. 清理过期的运行记录 (BackupScheduleRun)
    # 注意：这里可能需要更精细的逻辑，因为如果某个 run 包含被保留的备份，可能不应该完全删除 run
    # 但为了简化，假设 run 记录本身过期可以删除，只要备份内容还在即可（或者如果 run 关联的 items 还在，可能需要保留 run）
    # 改进策略：只删除那些没有任何 item 关联的过期 run，或者直接删除过期 run (因为 run 主要是日志性质)
    # 现有逻辑是直接删除过期 run，这通常是可以接受的，因为 run 主要是为了查看执行历史
    stmt_runs = select(BackupScheduleRun.id).where(BackupScheduleRun.started_at < threshold)
    run_ids = list(session.exec(stmt_runs))
    if run_ids:
        # 删除对应的 run items (如果有遗漏)
        stmt_del_run_items = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.run_id.in_(run_ids))
        session.exec(stmt_del_run_items)
        # 删除 run
        stmt_del_runs = delete(BackupScheduleRun).where(BackupScheduleRun.id.in_(run_ids))
        session.exec(stmt_del_runs)
        
    session.commit()
    return len(record_ids_to_delete)


def count_users(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(User)).one())


def list_users(session: Session, *, limit: int | None = None, offset: int = 0) -> list[User]:
    stmt = select(User).order_by(User.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    username = (username or "").strip()
    if not username:
        return None
    return session.exec(select(User).where(User.username == username).limit(1)).first()


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: str,
    password_expired: bool = False,
    group_access_type: str = "all",
    allowed_group_ids: str | None = None,
) -> User:
    username = username.strip()
    role = role.strip()
    if role not in {"admin", "operator", "readonly"}:
        role = "readonly"
    if get_user_by_username(session, username) is not None:
        raise RuntimeError("Username already exists")
    user = User(
        username=username,
        role=role,
        password_hash=hash_password(password),
        password_expired=password_expired,
        group_access_type=group_access_type,
        allowed_group_ids=allowed_group_ids,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def create_audit_log(
    session: Session,
    *,
    user_id: int | None = None,
    username: str | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def list_audit_logs(
    session: Session,
    *,
    q: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AuditLog.username.like(like),
                AuditLog.details.like(like),
                AuditLog.resource_id.like(like),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(stmt))


def count_audit_logs(
    session: Session,
    *,
    q: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(AuditLog)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AuditLog.username.like(like),
                AuditLog.details.like(like),
                AuditLog.resource_id.like(like),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    
    return int(session.exec(stmt).one())


def update_user(
    session: Session,
    user_id: int,
    *,
    username: str | None = None,
    role: str | None = None,
    password: str | None = None,
    group_access_type: str | None = None,
    allowed_group_ids: str | None = None,
) -> User | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    
    if username is not None:
        username = username.strip()
        if username:
            existing = get_user_by_username(session, username)
            if existing is not None and existing.id != user.id:
                raise RuntimeError("Username already exists")
            user.username = username
            
    if role is not None:
        role = role.strip()
        if role in {"admin", "operator", "readonly"}:
            user.role = role

    if group_access_type is not None:
        user.group_access_type = group_access_type

    if allowed_group_ids is not None:
        user.allowed_group_ids = allowed_group_ids
            
    if password:
        user.password_hash = hash_password(password)
        
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def delete_user(session: Session, user_id: int) -> None:
    user = session.get(User, user_id)
    if user is None:
        return
    session.delete(user)
    session.commit()


def authenticate_user(session: Session, *, username: str, password: str) -> User | None:
    user = get_user_by_username(session, username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_dashboard_summary(session: Session) -> dict[str, Any]:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)

    total_devices = session.exec(select(func.count()).select_from(Device)).one()
    total_groups = session.exec(select(func.count()).select_from(DeviceGroup)).one()

    # 近24小时备份情况
    backup_24h_total = session.exec(
        select(func.count()).select_from(BackupRecord).where(BackupRecord.started_at >= day_ago)
    ).one()
    backup_24h_success = session.exec(
        select(func.count())
        .select_from(BackupRecord)
        .where(BackupRecord.started_at >= day_ago)
        .where(BackupRecord.success == True)
    ).one()

    success_rate = 0
    if backup_24h_total > 0:
        success_rate = round((backup_24h_success / backup_24h_total) * 100, 1)

    return {
        "total_devices": total_devices,
        "total_groups": total_groups,
        "backup_24h_total": backup_24h_total,
        "backup_24h_success": backup_24h_success,
        "backup_24h_fail": backup_24h_total - backup_24h_success,
        "success_rate": success_rate,
    }


def get_device_platform_stats(session: Session) -> list[dict[str, Any]]:
    # 统计各平台设备数量
    stmt = select(Device.platform, func.count(Device.id)).group_by(Device.platform)
    results = session.exec(stmt).all()
    return [{"name": r[0], "value": r[1]} for r in results]


def get_backup_trend_stats(session: Session, days: int = 7) -> dict[str, list]:
    # 统计过去 N 天的备份趋势
    now = datetime.utcnow()
    dates = []
    success_counts = []
    fail_counts = []

    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        date_str = d.strftime("%m-%d")
        dates.append(date_str)

        start_of_day = datetime(d.year, d.month, d.day)
        end_of_day = start_of_day + timedelta(days=1)

        success = session.exec(
            select(func.count())
            .select_from(BackupRecord)
            .where(BackupRecord.started_at >= start_of_day)
            .where(BackupRecord.started_at < end_of_day)
            .where(BackupRecord.success == True)
        ).one()

        fail = session.exec(
            select(func.count())
            .select_from(BackupRecord)
            .where(BackupRecord.started_at >= start_of_day)
            .where(BackupRecord.started_at < end_of_day)
            .where(BackupRecord.success == False)
            .where(BackupRecord.finished_at != None)
        ).one()

        success_counts.append(success)
        fail_counts.append(fail)

    return {
        "dates": dates,
        "success": success_counts,
        "fail": fail_counts,
    }


def get_group_health_stats(session: Session) -> list[dict[str, Any]]:
    # 统计各分组的备份成功率
    groups = session.exec(select(DeviceGroup)).all()
    stats = []
    for g in groups:
        # 获取该分组下所有设备的ID
        device_ids = session.exec(select(Device.id).where(Device.group_id == g.id)).all()
        if not device_ids:
            continue

        total = session.exec(
            select(func.count()).select_from(BackupRecord).where(BackupRecord.device_id.in_(device_ids))
        ).one()
        if total == 0:
            continue

        success = session.exec(
            select(func.count())
            .select_from(BackupRecord)
            .where(BackupRecord.device_id.in_(device_ids))
            .where(BackupRecord.success == True)
        ).one()

        stats.append({"name": g.name, "value": round((success / total) * 100, 1)})

    # 按成功率排序
    stats.sort(key=lambda x: x["value"], reverse=True)
    return stats


def get_config_change_heatmap_stats(session: Session, days: int = 30) -> dict[str, Any]:
    end = datetime.utcnow()
    start = end - timedelta(days=days - 1)
    start_day = datetime(start.year, start.month, start.day)
    end_day = datetime(end.year, end.month, end.day) + timedelta(days=1)

    devices = session.exec(select(Device.id)).all()
    counts: dict[str, int] = {}

    for did in devices:
        prev = session.exec(
            select(BackupRecord)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.success == True)
            .where(BackupRecord.started_at < start_day)
            .order_by(BackupRecord.started_at.desc())
            .limit(1)
        ).first()
        rows = session.exec(
            select(BackupRecord)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.success == True)
            .where(BackupRecord.started_at >= start_day)
            .where(BackupRecord.started_at < end_day)
            .order_by(BackupRecord.started_at.asc())
        ).all()
        for row in rows:
            if prev and prev.config_text and row.config_text and prev.config_text != row.config_text:
                key = row.started_at.strftime("%Y-%m-%d")
                counts[key] = counts.get(key, 0) + 1
            prev = row

    data: list[list[Any]] = []
    max_val = 0
    for i in range(days):
        d = start_day + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        val = counts.get(key, 0)
        if val > max_val:
            max_val = val
        data.append([key, val])

    return {
        "range": [start_day.strftime("%Y-%m-%d"), (end_day - timedelta(days=1)).strftime("%Y-%m-%d")],
        "data": data,
        "max": max_val,
    }


def create_login_log(
    session: Session,
    *,
    username: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    status: str = "fail",
    fail_reason: str | None = None,
) -> LoginLog:
    log = LoginLog(
        username=username,
        ip_address=ip_address,
        user_agent=user_agent,
        status=status,
        fail_reason=fail_reason,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def list_login_logs(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LoginLog]:
    stmt = select(LoginLog)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                LoginLog.username.like(like),
                LoginLog.ip_address.like(like),
            )
        )
    if status:
        stmt = stmt.where(LoginLog.status == status)
    
    stmt = stmt.order_by(LoginLog.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(stmt))


def count_login_logs(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(LoginLog)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                LoginLog.username.like(like),
                LoginLog.ip_address.like(like),
            )
        )
    if status:
        stmt = stmt.where(LoginLog.status == status)
    
    return int(session.exec(stmt).one())
