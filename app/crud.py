from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Any, TypeVar
from uuid import UUID

from sqlalchemy import case, desc, false, func, or_
from sqlmodel import Session, select, delete

from app.models import (
    AppSetting,
    ApiKey,
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
    Role,
    TaskEvent,
    User,
    WebshellRecord,
)
from app.services import task_state_service
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.auth import hash_password, verify_password

_UNSET = object()
_ModelT = TypeVar("_ModelT")

PERMISSION_CATALOG = [
    {"code": "dashboard.view", "name": "仪表盘查看", "group": "dashboard"},
    {"code": "devices.view", "name": "设备查看", "group": "devices"},
    {"code": "devices.create", "name": "设备新增", "group": "devices"},
    {"code": "devices.update", "name": "设备修改", "group": "devices"},
    {"code": "devices.delete", "name": "设备删除", "group": "devices"},
    {"code": "devices.backup", "name": "设备备份", "group": "devices"},
    {"code": "devices.webshell", "name": "设备 WebShell", "group": "devices"},
    {"code": "groups.view", "name": "分组查看", "group": "groups"},
    {"code": "groups.create", "name": "分组新增", "group": "groups"},
    {"code": "groups.update", "name": "分组修改", "group": "groups"},
    {"code": "groups.delete", "name": "分组删除", "group": "groups"},
    {"code": "credentials.view", "name": "凭据查看", "group": "credentials"},
    {"code": "credentials.create", "name": "凭据新增", "group": "credentials"},
    {"code": "credentials.update", "name": "凭据修改", "group": "credentials"},
    {"code": "credentials.delete", "name": "凭据删除", "group": "credentials"},
    {"code": "templates.view", "name": "模板查看", "group": "templates"},
    {"code": "templates.create", "name": "模板新增", "group": "templates"},
    {"code": "templates.update", "name": "模板修改", "group": "templates"},
    {"code": "templates.delete", "name": "模板删除", "group": "templates"},
    {"code": "backups.view", "name": "备份历史查看", "group": "backups"},
    {"code": "backups.delete", "name": "备份历史删除", "group": "backups"},
    {"code": "config_search.view", "name": "配置搜索查看", "group": "config_search"},
    {"code": "schedules.view", "name": "定时任务查看", "group": "schedules"},
    {"code": "schedules.create", "name": "定时任务新增", "group": "schedules"},
    {"code": "schedules.update", "name": "定时任务修改", "group": "schedules"},
    {"code": "schedules.delete", "name": "定时任务删除", "group": "schedules"},
    {"code": "audit_logs.view", "name": "操作日志查看", "group": "audit_logs"},
    {"code": "webshell_records.view", "name": "录像查看", "group": "webshell_records"},
    {"code": "login_logs.view", "name": "登录日志查看", "group": "login_logs"},
    {"code": "diff_rules.view", "name": "Diff规则查看", "group": "diff_rules"},
    {"code": "diff_rules.update", "name": "Diff规则修改", "group": "diff_rules"},
    {"code": "diff_rules.delete", "name": "Diff规则删除", "group": "diff_rules"},
    {"code": "notifications.view", "name": "通知设置查看", "group": "notifications"},
    {"code": "notifications.update", "name": "通知设置修改", "group": "notifications"},
    {"code": "settings.view", "name": "系统设置查看", "group": "settings"},
    {"code": "settings.update", "name": "系统设置修改", "group": "settings"},
    {"code": "storage_settings.view", "name": "存储设置查看", "group": "storage_settings"},
    {"code": "storage_settings.update", "name": "存储设置修改", "group": "storage_settings"},
    {"code": "api_keys.view", "name": "API Key查看", "group": "api_keys"},
    {"code": "api_keys.create", "name": "API Key新增", "group": "api_keys"},
    {"code": "api_keys.delete", "name": "API Key删除", "group": "api_keys"},
    {"code": "users.view", "name": "用户查看", "group": "users"},
    {"code": "users.create", "name": "用户新增", "group": "users"},
    {"code": "users.update", "name": "用户修改", "group": "users"},
    {"code": "users.delete", "name": "用户删除", "group": "users"},
    {"code": "roles.view", "name": "角色查看", "group": "roles"},
    {"code": "roles.create", "name": "角色新增", "group": "roles"},
    {"code": "roles.update", "name": "角色修改", "group": "roles"},
    {"code": "roles.delete", "name": "角色删除", "group": "roles"},
]

LEGACY_PERMISSION_EXPANSIONS = {
    "devices.manage": {"devices.view", "devices.create", "devices.update", "devices.delete", "devices.backup", "devices.webshell"},
    "resources.view": {"groups.view", "credentials.view", "templates.view"},
    "resources.create": {"groups.create", "credentials.create", "templates.create"},
    "resources.update": {"groups.update", "credentials.update", "templates.update"},
    "resources.delete": {"groups.delete", "credentials.delete", "templates.delete"},
    "backups.trigger": {"devices.backup"},
    "groups.manage": {"groups.view", "groups.create", "groups.update", "groups.delete"},
    "credentials.manage": {"credentials.view", "credentials.create", "credentials.update", "credentials.delete"},
    "templates.manage": {"templates.view", "templates.create", "templates.update", "templates.delete"},
    "backups.manage": {"backups.view", "devices.backup", "backups.delete"},
    "schedules.manage": {"schedules.view", "schedules.create", "schedules.update", "schedules.delete"},
    "diff_rules.manage": {"diff_rules.view", "diff_rules.update", "diff_rules.delete"},
    "notifications.manage": {"notifications.view", "notifications.update"},
    "settings.manage": {"settings.view", "settings.update"},
    "storage_settings.manage": {"storage_settings.view", "storage_settings.update"},
    "api_keys.manage": {"api_keys.view", "api_keys.create", "api_keys.delete"},
    "users.manage": {"users.view", "users.create", "users.update", "users.delete"},
    "roles.manage": {"roles.view", "roles.create", "roles.update", "roles.delete"},
}

BUILTIN_ROLE_DEFAULTS = {
    "operator": {
        "dashboard.view",
        "devices.view",
        "devices.create",
        "devices.update",
        "devices.delete",
        "devices.backup",
        "devices.webshell",
        "groups.view",
        "groups.create",
        "groups.update",
        "groups.delete",
        "credentials.view",
        "credentials.create",
        "credentials.update",
        "credentials.delete",
        "templates.view",
        "templates.create",
        "templates.update",
        "templates.delete",
        "backups.view",
        "backups.delete",
        "config_search.view",
        "schedules.view",
        "schedules.create",
        "schedules.update",
        "schedules.delete",
        "audit_logs.view",
        "webshell_records.view",
        "login_logs.view",
    },
    "readonly": {
        "dashboard.view",
        "devices.view",
        "groups.view",
        "credentials.view",
        "templates.view",
        "backups.view",
        "config_search.view",
        "schedules.view",
        "audit_logs.view",
        "webshell_records.view",
        "login_logs.view",
    },
}

BUILTIN_ROLE_LABELS = {
    "admin": "系统管理员",
    "operator": "操作员",
    "readonly": "只读用户",
}

ROLE_DEFAULT_PERMISSIONS = {k: set(v) for k, v in BUILTIN_ROLE_DEFAULTS.items()}
ROLE_LABELS = dict(BUILTIN_ROLE_LABELS)
ROLE_ADMIN_CODES = {"admin"}


def _flush(session: Session) -> None:
    session.flush()


def _flush_and_refresh(session: Session, instance: _ModelT) -> _ModelT:
    session.flush()
    session.refresh(instance)
    return instance

def list_permission_catalog() -> list[dict[str, str]]:
    return PERMISSION_CATALOG

def _expand_permission_codes(codes: Iterable[str]) -> set[str]:
    expanded: set[str] = set()
    for raw in codes:
        code = (raw or "").strip()
        if not code:
            continue
        if code in LEGACY_PERMISSION_EXPANSIONS:
            expanded |= LEGACY_PERMISSION_EXPANSIONS[code]
        else:
            expanded.add(code)
    return expanded

def normalize_permission_codes(codes: Iterable[str]) -> list[str]:
    allowed = {p["code"] for p in PERMISSION_CATALOG}
    expanded = _expand_permission_codes(codes)
    normalized = sorted({c for c in expanded if c in allowed})
    return normalized

def permission_codes_to_str(codes: Iterable[str]) -> str | None:
    normalized = normalize_permission_codes(codes)
    return ",".join(normalized) if normalized else None

def parse_permission_codes(raw: str | None) -> set[str]:
    allowed = {p["code"] for p in PERMISSION_CATALOG}
    expanded = _expand_permission_codes((raw or "").split(","))
    return {c for c in expanded if c in allowed}

def refresh_role_cache(session: Session) -> None:
    global ROLE_DEFAULT_PERMISSIONS, ROLE_LABELS, ROLE_ADMIN_CODES
    roles = list(session.exec(select(Role).order_by(Role.id)))
    if not roles:
        ROLE_DEFAULT_PERMISSIONS = {k: set(v) for k, v in BUILTIN_ROLE_DEFAULTS.items()}
        ROLE_LABELS = dict(BUILTIN_ROLE_LABELS)
        ROLE_ADMIN_CODES = {"admin"}
        return

    defaults: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    admin_codes: set[str] = set()
    for role in roles:
        code = (role.code or "").strip()
        if not code:
            continue
        labels[code] = (role.name or code).strip()
        if role.is_admin or code == "admin":
            admin_codes.add(code)
            continue
        defaults[code] = parse_permission_codes(role.permissions)

    if "operator" not in defaults and "operator" in BUILTIN_ROLE_DEFAULTS:
        defaults["operator"] = set(BUILTIN_ROLE_DEFAULTS["operator"])
    if "readonly" not in defaults and "readonly" in BUILTIN_ROLE_DEFAULTS:
        defaults["readonly"] = set(BUILTIN_ROLE_DEFAULTS["readonly"])
    if not admin_codes:
        admin_codes.add("admin")

    ROLE_DEFAULT_PERMISSIONS = defaults
    ROLE_LABELS = labels
    ROLE_ADMIN_CODES = admin_codes

def ensure_default_roles(session: Session) -> None:
    existing = set(session.exec(select(Role.code)).all())
    seeds = [
        {
            "code": "admin",
            "name": BUILTIN_ROLE_LABELS["admin"],
            "permissions": None,
            "is_system": True,
            "is_admin": True,
        },
        {
            "code": "operator",
            "name": BUILTIN_ROLE_LABELS["operator"],
            "permissions": ",".join(sorted(BUILTIN_ROLE_DEFAULTS["operator"])),
            "is_system": True,
            "is_admin": False,
        },
        {
            "code": "readonly",
            "name": BUILTIN_ROLE_LABELS["readonly"],
            "permissions": ",".join(sorted(BUILTIN_ROLE_DEFAULTS["readonly"])),
            "is_system": True,
            "is_admin": False,
        },
    ]
    created = False
    for seed in seeds:
        if seed["code"] in existing:
            continue
        role = Role(
            code=seed["code"],
            name=seed["name"],
            permissions=seed["permissions"],
            is_system=seed["is_system"],
            is_admin=seed["is_admin"],
        )
        session.add(role)
        created = True
    if created:
        _flush(session)
    refresh_role_cache(session)

def is_admin_role_code(code: str | None) -> bool:
    return (code or "").strip() in ROLE_ADMIN_CODES

def get_role_label(code: str | None) -> str:
    raw = (code or "").strip()
    return ROLE_LABELS.get(raw, raw)

def get_role_default_permissions(code: str | None) -> set[str]:
    raw = (code or "").strip()
    return set(ROLE_DEFAULT_PERMISSIONS.get(raw, set()))

def get_effective_permission_codes(user: User | None) -> set[str]:
    if not user:
        return set()
    if is_admin_role_code(user.role):
        return {p["code"] for p in PERMISSION_CATALOG}
    perms: set[str] = set()
    perms |= get_role_default_permissions(user.role)
    return perms

def list_roles(session: Session) -> list[Role]:
    return list(session.exec(select(Role).order_by(Role.id)))

def count_roles(session: Session) -> int:
    stmt = select(func.count()).select_from(Role)
    return int(session.exec(stmt).one())

def get_role(session: Session, role_id: int) -> Role | None:
    return session.get(Role, role_id)

def get_role_by_code(session: Session, code: str) -> Role | None:
    target = (code or "").strip()
    if not target:
        return None
    return session.exec(select(Role).where(Role.code == target)).first()

def create_role(
    session: Session,
    *,
    code: str,
    name: str,
    permissions: str | None = None,
    is_system: bool = False,
    is_admin: bool = False,
) -> Role:
    normalized_code = (code or "").strip().lower()
    if not normalized_code:
        raise RuntimeError("角色标识不能为空")
    if get_role_by_code(session, normalized_code) is not None:
        raise RuntimeError("角色标识已存在")
    role = Role(
        code=normalized_code,
        name=name.strip(),
        permissions=permissions,
        is_system=is_system,
        is_admin=is_admin,
    )
    session.add(role)
    _flush_and_refresh(session, role)
    refresh_role_cache(session)
    return role

def update_role(
    session: Session,
    role_id: int,
    *,
    code: str | None = None,
    name: str | None = None,
    permissions: str | None = None,
) -> Role | None:
    role = session.get(Role, role_id)
    if role is None:
        return None
    if code is not None:
        normalized_code = (code or "").strip().lower()
        if not normalized_code:
            raise RuntimeError("角色标识不能为空")
        existing = get_role_by_code(session, normalized_code)
        if existing is not None and existing.id != role.id:
            raise RuntimeError("角色标识已存在")
        if normalized_code != role.code:
            old_code = role.code
            role.code = normalized_code
            users = session.exec(select(User).where(User.role == old_code)).all()
            for user in users:
                user.role = normalized_code
                session.add(user)
    if name is not None:
        role.name = name.strip()
    if permissions is not None:
        role.permissions = permissions
    session.add(role)
    _flush_and_refresh(session, role)
    refresh_role_cache(session)
    return role

def delete_role(session: Session, role_id: int) -> None:
    role = session.get(Role, role_id)
    if role is None:
        return
    session.delete(role)
    _flush(session)
    refresh_role_cache(session)

def role_usage_count(session: Session, code: str) -> int:
    target = (code or "").strip()
    if not target:
        return 0
    stmt = select(func.count()).select_from(User).where(User.role == target)
    return int(session.exec(stmt).one())


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
    allowed_group_ids: list[int] | None = None,
) -> list[Device]:
    stmt = select(Device)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Device.name.ilike(like), Device.host.ilike(like)))
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
        filtered_group_ids = expand_group_ids(session, [group_id], include_special_ids=False)
        if not filtered_group_ids:
            return []
        stmt = stmt.where(Device.group_id.in_(filtered_group_ids))
    
    if allowed_group_ids is not None:
        allowed_set = set(allowed_group_ids)
        # Handle ungrouped devices: if -1 or 0 is in allowed_set, include devices with group_id=None or group_id=0
        include_ungrouped = (-1 in allowed_set or 0 in allowed_set)
        
        real_ids = [i for i in allowed_group_ids if i > 0]
        
        conditions = []
        if real_ids:
            conditions.append(Device.group_id.in_(real_ids))
        if include_ungrouped:
            conditions.append(or_(Device.group_id == None, Device.group_id == 0))
        
        if conditions:
            stmt = stmt.where(or_(*conditions))
        else:
            # If no real IDs and no ungrouped allowed, return nothing
            return []

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
    allowed_group_ids: list[int] | None = None,
) -> int:
    stmt = select(func.count()).select_from(Device)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Device.name.ilike(like), Device.host.ilike(like)))
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
        filtered_group_ids = expand_group_ids(session, [group_id], include_special_ids=False)
        if not filtered_group_ids:
            return 0
        stmt = stmt.where(Device.group_id.in_(filtered_group_ids))

    if allowed_group_ids is not None:
        allowed_set = set(allowed_group_ids)
        include_ungrouped = (-1 in allowed_set or 0 in allowed_set)
        real_ids = [i for i in allowed_group_ids if i > 0]
        
        conditions = []
        if real_ids:
            conditions.append(Device.group_id.in_(real_ids))
        if include_ungrouped:
            conditions.append(or_(Device.group_id == None, Device.group_id == 0))
        
        if conditions:
            stmt = stmt.where(or_(*conditions))
        else:
            return 0

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
    return _flush_and_refresh(session, device)


def delete_device(session: Session, device_id: int, commit: bool = True) -> None:
    device = session.get(Device, device_id)
    if device is None:
        return
    
    # 1. 删除关联的备份记录 (BackupRecord)
    stmt_backups = delete(BackupRecord).where(BackupRecord.device_id == device_id)
    session.exec(stmt_backups)
    
    # 2. 删除关联的计划运行明细 (BackupScheduleRunItem)
    affected_run_ids = session.exec(
        select(BackupScheduleRunItem.run_id).where(BackupScheduleRunItem.device_id == device_id)
    ).all()
    stmt_run_items = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.device_id == device_id)
    session.exec(stmt_run_items)
    _delete_empty_schedule_runs(session, affected_run_ids)
    
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
        _flush(session)


def update_device(
    session: Session,
    device_id: int,
    *,
    name: str,
    host: str,
    port: int,
    login_method: str,
    encoding: str,
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
    device.encoding = (encoding or "utf-8").strip() or "utf-8"
    device.platform = platform
    device.group_id = group_id
    device.credential_id = credential_id
    device.default_template_id = default_template_id
    session.add(device)
    return _flush_and_refresh(session, device)


def count_groups(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(DeviceGroup)).one())


def list_groups(session: Session, *, limit: int | None = None, offset: int = 0) -> list[DeviceGroup]:
    stmt = select(DeviceGroup).order_by(DeviceGroup.depth, DeviceGroup.path, DeviceGroup.sort_order, DeviceGroup.id)
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt))


def get_group_by_name(session: Session, name: str) -> DeviceGroup | None:
    normalized = (name or "").strip()
    if not normalized:
        return None
    return session.exec(select(DeviceGroup).where(DeviceGroup.name == normalized).limit(1)).first()


def _build_group_path(*, group_id: int, parent: DeviceGroup | None) -> str:
    if parent is None:
        return f"/{group_id}/"
    return f"{parent.path}{group_id}/"


def _depth_from_group_path(path: str) -> int:
    normalized = (path or "").strip()
    if not normalized:
        return 0
    return max(normalized.count("/") - 2, 0)


def expand_group_ids(
    session: Session,
    group_ids: Iterable[int] | None,
    *,
    include_special_ids: bool = True,
) -> list[int]:
    if group_ids is None:
        return []

    raw_ids = {int(group_id) for group_id in group_ids if group_id is not None}
    special_ids = {group_id for group_id in raw_ids if group_id <= 0}
    positive_ids = {group_id for group_id in raw_ids if group_id > 0}
    if not positive_ids:
        return sorted(special_ids) if include_special_ids else []

    groups = list_groups(session)
    selected_paths = {
        (group.path or "").strip()
        for group in groups
        if group.id and int(group.id) in positive_ids and (group.path or "").strip()
    }
    expanded_ids = {
        int(group.id)
        for group in groups
        if group.id and any((group.path or "").startswith(path) for path in selected_paths)
    }
    if include_special_ids:
        expanded_ids |= special_ids
    return sorted(expanded_ids)


def group_child_count(session: Session, group_id: int) -> int:
    stmt = select(func.count()).select_from(DeviceGroup).where(DeviceGroup.parent_id == group_id)
    return int(session.exec(stmt).one())


def group_usage_count(session: Session, group_id: int) -> int:
    stmt = select(func.count()).where(Device.group_id == group_id)
    return int(session.exec(stmt).one())


def group_subtree_usage_count(session: Session, group_id: int) -> int:
    subtree_ids = expand_group_ids(session, [group_id], include_special_ids=False)
    if not subtree_ids:
        return 0
    stmt = select(func.count()).select_from(Device).where(Device.group_id.in_(subtree_ids))
    return int(session.exec(stmt).one())


def get_group(session: Session, group_id: int) -> DeviceGroup | None:
    return session.get(DeviceGroup, group_id)


def create_group(
    session: Session,
    *,
    name: str,
    parent_id: int | None = None,
    sort_order: int = 0,
) -> DeviceGroup:
    parent = get_group(session, int(parent_id)) if parent_id else None
    if parent_id and parent is None:
        raise RuntimeError("Parent group not found")
    group = DeviceGroup(
        name=name.strip(),
        parent_id=int(parent_id) if parent_id else None,
        sort_order=int(sort_order or 0),
    )
    session.add(group)
    _flush_and_refresh(session, group)
    if group.id is None:
        return group
    group.path = _build_group_path(group_id=int(group.id), parent=parent)
    group.depth = int(parent.depth + 1) if parent is not None else 0
    session.add(group)
    return _flush_and_refresh(session, group)


def delete_group(session: Session, group_id: int) -> None:
    group = session.get(DeviceGroup, group_id)
    if group is None:
        return
    session.delete(group)
    _flush(session)


def update_group(
    session: Session,
    group_id: int,
    *,
    name: str,
    parent_id: int | None | object = _UNSET,
    sort_order: int | None | object = _UNSET,
) -> DeviceGroup | None:
    group = session.get(DeviceGroup, group_id)
    if group is None:
        return None
    old_path = (group.path or "").strip() or f"/{group_id}/"
    group.name = name.strip()
    if sort_order is not _UNSET:
        group.sort_order = int(sort_order or 0)

    if parent_id is not _UNSET:
        normalized_parent_id = int(parent_id) if parent_id else None
        if normalized_parent_id == int(group.id):
            raise RuntimeError("Group cannot be its own parent")
        parent = get_group(session, normalized_parent_id) if normalized_parent_id else None
        if normalized_parent_id and parent is None:
            raise RuntimeError("Parent group not found")
        if parent is not None and (parent.path or "").startswith(old_path):
            raise RuntimeError("Cannot move group under its descendant")

        group.parent_id = normalized_parent_id
        group.path = _build_group_path(group_id=int(group.id), parent=parent)
        group.depth = int(parent.depth + 1) if parent is not None else 0

    session.add(group)
    _flush_and_refresh(session, group)

    new_path = (group.path or "").strip() or old_path
    if new_path != old_path:
        descendants = list(
            session.exec(
                select(DeviceGroup)
                .where(DeviceGroup.path.like(f"{old_path}%"))
                .where(DeviceGroup.id != group.id)
            )
        )
        for item in descendants:
            suffix = (item.path or "")[len(old_path):]
            item.path = f"{new_path}{suffix}"
            item.depth = _depth_from_group_path(item.path)
            session.add(item)

    return _flush_and_refresh(session, group)


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
    return _flush_and_refresh(session, credential)


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
        credential.password = password
    if enable_password is not None:
        credential.enable_password = enable_password
    credential.remarks = remarks
    session.add(credential)
    return _flush_and_refresh(session, credential)


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
    _flush(session)


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
    return _flush_and_refresh(session, template)


def delete_template(session: Session, template_id: int) -> None:
    template = session.get(BackupTemplate, template_id)
    if template is None:
        return
    session.delete(template)
    _flush(session)


def update_template(session: Session, template_id: int, *, name: str, platform: str, commands: str) -> BackupTemplate | None:
    template = session.get(BackupTemplate, template_id)
    if template is None:
        return None
    template.name = name
    template.platform = platform
    template.commands = commands
    session.add(template)
    return _flush_and_refresh(session, template)


def create_backup_record(
    session: Session,
    *,
    device_id: int,
    template_id: int | None,
) -> BackupRecord:
    record = BackupRecord(
        device_id=device_id,
        template_id=template_id,
        status=task_state_service.BACKUP_RECORD_STATUS_PLANNED,
        success=False,
    )
    session.add(record)
    return _flush_and_refresh(session, record)


def update_backup_record_status(
    session: Session,
    *,
    record_id: UUID,
    status: str,
) -> BackupRecord | None:
    record = session.get(BackupRecord, record_id)
    if record is None:
        return None
    record.status = (status or "").strip() or task_state_service.BACKUP_RECORD_STATUS_PLANNED
    session.add(record)
    return _flush_and_refresh(session, record)


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
    record.status = task_state_service.backup_record_terminal_status(success=bool(success))
    record.finished_at = datetime.utcnow()
    record.success = success
    record.config_text = config_text
    record.error_message = error_message
    record.duration_seconds = duration_seconds
    record.failure_type = failure_type
    session.add(record)
    return _flush_and_refresh(session, record)


def cancel_backup_record(
    session: Session,
    *,
    record_id: UUID,
    error_message: str | None = None,
    failure_type: str | None = None,
) -> BackupRecord | None:
    record = session.get(BackupRecord, record_id)
    if record is None:
        return None
    record.status = task_state_service.BACKUP_RECORD_STATUS_CANCELLED
    record.finished_at = datetime.utcnow()
    record.success = False
    record.error_message = (error_message or "").strip() or "Task cancelled"
    record.failure_type = (failure_type or "").strip() or "CANCELLED"
    session.add(record)
    return _flush_and_refresh(session, record)


def list_backups(session: Session, *, limit: int = 50) -> list[BackupRecord]:
    stmt = select(BackupRecord).order_by(BackupRecord.started_at.desc()).limit(limit)
    return list(session.exec(stmt))


def get_latest_backups_per_device(session: Session) -> list[BackupRecord]:
    subq = (
        select(
            BackupRecord.device_id,
            func.max(BackupRecord.started_at).label("max_time")
        )
        .group_by(BackupRecord.device_id)
        .subquery()
    )
    
    stmt = (
        select(BackupRecord)
        .join(
            subq,
            (BackupRecord.device_id == subq.c.device_id) &
            (BackupRecord.started_at == subq.c.max_time)
        )
        .order_by(BackupRecord.started_at.desc())
    )
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


def has_active_backups_for_device(session: Session, device_id: int) -> bool:
    stmt = (
        select(BackupRecord.id)
        .where(BackupRecord.device_id == device_id)
        .where(BackupRecord.status.in_(task_state_service.BACKUP_RECORD_ACTIVE_STATUSES))
        .limit(1)
    )
    return session.exec(stmt).first() is not None


def get_backup(session: Session, backup_id: UUID) -> BackupRecord | None:
    return session.get(BackupRecord, backup_id)


def list_backups_by_ids(session: Session, backup_ids: Iterable[UUID]) -> list[BackupRecord]:
    ids = list(backup_ids)
    if not ids:
        return []
    stmt = select(BackupRecord).where(BackupRecord.id.in_(ids)).order_by(BackupRecord.started_at.desc())
    return list(session.exec(stmt))


def _config_search_condition(session: Session, keyword: str):
    q = (keyword or "").strip()
    if not q:
        return None
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        text_expr = func.coalesce(BackupRecord.config_text, "")
        ts_vector = func.to_tsvector("simple", text_expr)
        ts_query = func.websearch_to_tsquery("simple", q)
        return or_(ts_vector.op("@@")(ts_query), BackupRecord.config_text.ilike(f"%{q}%"))
    return BackupRecord.config_text.like(f"%{q}%")


def _allowed_device_groups_condition(allowed_group_ids: list[int] | None):
    if allowed_group_ids is None:
        return None

    allowed_set = {int(group_id) for group_id in allowed_group_ids}
    include_ungrouped = (-1 in allowed_set) or (0 in allowed_set)
    real_ids = [group_id for group_id in allowed_set if group_id > 0]

    conditions = []
    if real_ids:
        conditions.append(Device.group_id.in_(real_ids))
    if include_ungrouped:
        conditions.append(or_(Device.group_id.is_(None), Device.group_id == 0))
    if not conditions:
        return false()
    return or_(*conditions)


def search_config(
    session: Session,
    *,
    q: str,
    latest_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> list[BackupRecord]:
    stmt = (
        select(BackupRecord)
        .join(Device, BackupRecord.device_id == Device.id)
        .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
    )
    condition = _config_search_condition(session, q)
    if condition is not None:
        stmt = stmt.where(condition)
    group_condition = _allowed_device_groups_condition(allowed_group_ids)
    if group_condition is not None:
        stmt = stmt.where(group_condition)

    if latest_only:
        subq = (
            select(
                BackupRecord.device_id,
                func.max(BackupRecord.started_at).label("max_started"),
            )
            .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
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
    allowed_group_ids: list[int] | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(BackupRecord)
        .join(Device, BackupRecord.device_id == Device.id)
        .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
    )
    condition = _config_search_condition(session, q)
    if condition is not None:
        stmt = stmt.where(condition)
    group_condition = _allowed_device_groups_condition(allowed_group_ids)
    if group_condition is not None:
        stmt = stmt.where(group_condition)

    if latest_only:
        subq = (
            select(
                BackupRecord.device_id,
                func.max(BackupRecord.started_at).label("max_started"),
            )
            .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
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


# -----------------------------------------------------------------------------
# ApiKey Functions
# -----------------------------------------------------------------------------

def get_api_key_by_hash(session: Session, key_hash: str) -> ApiKey | None:
    return session.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()

def get_api_keys(session: Session, skip: int = 0, limit: int = 100) -> list[ApiKey]:
    return list(session.exec(select(ApiKey).order_by(ApiKey.created_at.desc()).offset(skip).limit(limit)).all())

def count_api_keys(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(ApiKey)).one())

def create_api_key(session: Session, *, api_key: ApiKey) -> ApiKey:
    session.add(api_key)
    return _flush_and_refresh(session, api_key)

def update_api_key_last_used(session: Session, key_id: int) -> None:
    api_key = session.get(ApiKey, key_id)
    if api_key:
        api_key.last_used_at = datetime.utcnow()
        session.add(api_key)
        _flush(session)

def revoke_api_key(session: Session, key_id: int) -> bool:
    api_key = session.get(ApiKey, key_id)
    if api_key:
        api_key.is_active = False
        session.add(api_key)
        _flush(session)
        return True
    return False

def delete_api_key(session: Session, key_id: int) -> bool:
    api_key = session.get(ApiKey, key_id)
    if api_key:
        session.delete(api_key)
        _flush(session)
        return True
    return False

def set_setting(session: Session, *, key: str, value: str) -> AppSetting:
    item = session.get(AppSetting, key)
    if item is None:
        item = AppSetting(key=key, value=value)
    else:
        item.value = value
        item.updated_at = datetime.utcnow()
    session.add(item)
    return _flush_and_refresh(session, item)


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
    return _flush_and_refresh(session, schedule)


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
    return _flush_and_refresh(session, item)


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
    _flush(session)


def has_active_runs_for_schedule(session: Session, schedule_id: int) -> bool:
    active_statuses = tuple(task_state_service.SCHEDULE_RUN_ACTIVE_STATUSES)
    if not active_statuses:
        return False
    stmt = (
        select(BackupScheduleRun.id)
        .where(BackupScheduleRun.schedule_id == int(schedule_id))
        .where(BackupScheduleRun.status.in_(active_statuses))
        .limit(1)
    )
    return session.exec(stmt).first() is not None


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
        status=task_state_service.SCHEDULE_RUN_STATUS_PLANNED,
        total_devices=int(total_devices or 0),
        success_count=0,
        fail_count=0,
        error_message=None,
    )
    session.add(run)
    return _flush_and_refresh(session, run)


def get_schedule_run(session: Session, run_id: UUID) -> BackupScheduleRun | None:
    return session.get(BackupScheduleRun, run_id)


def update_schedule_run_status(
    session: Session,
    *,
    run_id: UUID,
    status: str,
) -> BackupScheduleRun | None:
    run = session.get(BackupScheduleRun, run_id)
    if run is None:
        return None
    run.status = (status or "").strip() or task_state_service.SCHEDULE_RUN_STATUS_PLANNED
    session.add(run)
    return _flush_and_refresh(session, run)


def finish_schedule_run(
    session: Session,
    *,
    run_id: UUID,
    success_count: int,
    fail_count: int,
    error_message: str | None,
    status: str | None = None,
    cancelled_count: int = 0,
    unfinished_count: int = 0,
) -> BackupScheduleRun | None:
    run = session.get(BackupScheduleRun, run_id)
    if run is None:
        return None
    run.status = (status or "").strip() or task_state_service.derive_schedule_run_terminal_status(
        total_devices=int(run.total_devices or 0),
        success_count=int(success_count or 0),
        fail_count=int(fail_count or 0),
        cancelled_count=int(cancelled_count or 0),
        unfinished_count=int(unfinished_count or 0),
    )
    run.finished_at = datetime.utcnow()
    run.success_count = int(success_count or 0)
    run.fail_count = int(fail_count or 0)
    run.error_message = (error_message or "").strip() or None
    session.add(run)
    return _flush_and_refresh(session, run)


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
    return _flush_and_refresh(session, item)


def list_schedule_run_items(session: Session, run_id: UUID) -> list[BackupScheduleRunItem]:
    stmt = select(BackupScheduleRunItem).where(BackupScheduleRunItem.run_id == run_id).order_by(BackupScheduleRunItem.id)
    return list(session.exec(stmt))


def _delete_empty_schedule_runs(session: Session, run_ids: Iterable[UUID]) -> None:
    candidate_run_ids = list({run_id for run_id in run_ids if run_id})
    if not candidate_run_ids:
        return

    candidate_runs = session.exec(
        select(BackupScheduleRun).where(BackupScheduleRun.id.in_(candidate_run_ids))
    ).all()
    terminal_run_ids = {
        run.id
        for run in candidate_runs
        if getattr(run, "id", None) and task_state_service.is_schedule_run_terminal_status(run.status)
    }
    if not terminal_run_ids:
        return

    remaining_run_ids = set(
        session.exec(
            select(BackupScheduleRunItem.run_id)
            .where(BackupScheduleRunItem.run_id.in_(terminal_run_ids))
            .group_by(BackupScheduleRunItem.run_id)
        ).all()
    )
    run_ids_to_delete = [run_id for run_id in terminal_run_ids if run_id not in remaining_run_ids]
    if run_ids_to_delete:
        stmt_del_runs = delete(BackupScheduleRun).where(BackupScheduleRun.id.in_(run_ids_to_delete))
        session.exec(stmt_del_runs)


def bulk_delete_backups(session: Session, backup_ids: Iterable[UUID]) -> int:
    count = 0
    affected_run_ids: set[UUID] = set()
    for bid in backup_ids:
        record = session.get(BackupRecord, bid)
        if record is None:
            continue
        # 同时删除关联的 run items
        affected_run_ids.update(
            session.exec(
                select(BackupScheduleRunItem.run_id).where(BackupScheduleRunItem.backup_id == bid)
            ).all()
        )
        stmt = delete(BackupScheduleRunItem).where(BackupScheduleRunItem.backup_id == bid)
        session.exec(stmt)
        session.delete(record)
        count += 1
    _delete_empty_schedule_runs(session, affected_run_ids)
    _flush(session)
    return count


def cleanup_old_backups(session: Session, days: int) -> int:
    """清理指定天数之前的备份记录和运行日志"""
    if days <= 0:
        return 0
    
    threshold = datetime.utcnow() - timedelta(days=days)
    
    # 1. 查找过期的备份记录
    stmt_records = select(BackupRecord.id, BackupRecord.device_id, BackupRecord.started_at, BackupRecord.success).where(BackupRecord.started_at < threshold)
    records_to_check = session.exec(stmt_records).all()
    
    # 兜底保护：确保每台设备至少保留一份最近的成功备份
    # 策略：
    # 1. 找出所有涉及的 device_id
    # 2. 对每个 device，查询其最近一次成功的备份 ID
    # 3. 如果该 ID 在待删除列表中，将其移除

    device_ids = {r.device_id for r in records_to_check}
    latest_success_map = {}  # device_id -> backup_id

    for did in device_ids:
        # 查询该设备最近一次成功的备份
        stmt_latest = (
            select(BackupRecord.id)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
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
    # 只删除那些已经没有任何 run item 的过期 run，避免把仍关联保留备份的运行历史删掉。
    stmt_runs = select(BackupScheduleRun.id).where(BackupScheduleRun.started_at < threshold)
    run_ids = list(session.exec(stmt_runs))
    _delete_empty_schedule_runs(session, run_ids)

    _flush(session)
    return len(record_ids_to_delete)


import os

def cleanup_old_webshell_records(session: Session, days: int) -> int:
    """清理指定天数之前的 Webshell 录像记录及对应文件"""
    if days <= 0:
        return 0

    threshold = datetime.utcnow() - timedelta(days=days)
    
    # 查找过期的录像记录
    stmt_records = select(WebshellRecord).where(WebshellRecord.started_at < threshold)
    records_to_delete = session.exec(stmt_records).all()

    if not records_to_delete:
        return 0

    count = 0
    for record in records_to_delete:
        # 删除本地文件
        if record.file_path and os.path.exists(record.file_path):
            try:
                os.remove(record.file_path)
            except Exception as e:
                pass # 可选地添加日志： logger.warning(f"Failed to delete webshell record file {record.file_path}: {e}")

        session.delete(record)
        count += 1

    _flush(session)
    return count


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
    mfa_enabled: bool = False,
    mfa_secret: str | None = None,
) -> User:
    username = username.strip()
    role = (role or "").strip().lower()
    if not role or get_role_by_code(session, role) is None:
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
        mfa_enabled=mfa_enabled,
    )
    if mfa_secret:
        user.mfa_secret = mfa_secret
    session.add(user)
    return _flush_and_refresh(session, user)


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
    return _flush_and_refresh(session, log)


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
    mfa_enabled: bool | None = None,
    mfa_secret: str | None | object = _UNSET,
    recovery_codes: list[str] | None | object = _UNSET,
    recovery_codes_enabled: bool | None = None,
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
        role = (role or "").strip().lower()
        if role and get_role_by_code(session, role) is not None:
            user.role = role

    if group_access_type is not None:
        user.group_access_type = group_access_type

    if allowed_group_ids is not None:
        user.allowed_group_ids = allowed_group_ids

    if password:
        user.password_hash = hash_password(password)

    if mfa_enabled is not None:
        user.mfa_enabled = bool(mfa_enabled)
        if not mfa_enabled:
            user.mfa_secret = None

    if mfa_secret is not _UNSET:
        user.mfa_secret = mfa_secret

    if recovery_codes is not _UNSET:
        user.recovery_codes = recovery_codes

    if recovery_codes_enabled is not None:
        user.recovery_codes_enabled = bool(recovery_codes_enabled)

    session.add(user)
    return _flush_and_refresh(session, user)


def delete_user(session: Session, user_id: int) -> None:
    user = session.get(User, user_id)
    if user is None:
        return
    session.delete(user)
    _flush(session)


def authenticate_user(session: Session, *, username: str, password: str) -> User | None:
    user = get_user_by_username(session, username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_dashboard_summary(session: Session, *, window_hours: int = 24) -> dict[str, Any]:
    now = datetime.utcnow()
    window_start = now - timedelta(hours=max(1, int(window_hours)))

    total_devices = session.exec(select(func.count()).select_from(Device)).one()
    total_groups = session.exec(select(func.count()).select_from(DeviceGroup)).one()

    backup_window_total = session.exec(
        select(func.count()).select_from(BackupRecord).where(BackupRecord.started_at >= window_start)
    ).one()
    backup_window_success = session.exec(
        select(func.count())
        .select_from(BackupRecord)
        .where(BackupRecord.started_at >= window_start)
        .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
    ).one()

    success_rate = 0
    if backup_window_total > 0:
        success_rate = round((backup_window_success / backup_window_total) * 100, 1)

    return {
        "total_devices": total_devices,
        "total_groups": total_groups,
        "window_hours": max(1, int(window_hours)),
        "backup_window_total": backup_window_total,
        "backup_window_success": backup_window_success,
        "backup_window_fail": backup_window_total - backup_window_success,
        "success_rate": success_rate,
    }


def get_device_platform_stats(session: Session) -> list[dict[str, Any]]:
    # 统计各平台设备数量
    stmt = select(Device.platform, func.count(Device.id)).group_by(Device.platform)
    results = session.exec(stmt).all()
    return [{"name": r[0], "value": r[1]} for r in results]


def get_backup_trend_stats(session: Session, *, window_key: str = "30d") -> dict[str, Any]:
    normalized_key = (window_key or "30d").strip().lower()
    if normalized_key not in {"24h", "7d", "30d"}:
        normalized_key = "30d"

    now = datetime.utcnow()
    if normalized_key == "24h":
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        start_at = current_hour - timedelta(hours=23)
        end_at = current_hour + timedelta(hours=1)
        labels = [(start_at + timedelta(hours=i)).strftime("%m-%d %H:00") for i in range(24)]
        grouped_counts = {label: {"success": 0, "fail": 0} for label in labels}
        granularity_label = "按小时"
    else:
        window_days = 7 if normalized_key == "7d" else 30
        start_at = datetime(now.year, now.month, now.day) - timedelta(days=window_days - 1)
        end_at = datetime(now.year, now.month, now.day) + timedelta(days=1)
        labels = [(start_at + timedelta(days=i)).strftime("%m-%d") for i in range(window_days)]
        grouped_counts = {label: {"success": 0, "fail": 0} for label in labels}
        granularity_label = "按天"

    rows = session.exec(
        select(BackupRecord.started_at, BackupRecord.status)
        .where(
            BackupRecord.started_at >= start_at,
            BackupRecord.started_at < end_at,
        )
        .order_by(BackupRecord.started_at)
    ).all()

    for started_at, status in rows:
        if started_at is None:
            continue
        bucket = started_at.strftime("%m-%d %H:00") if normalized_key == "24h" else started_at.strftime("%m-%d")
        if bucket not in grouped_counts:
            continue
        if status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED:
            grouped_counts[bucket]["success"] += 1
        elif status == task_state_service.BACKUP_RECORD_STATUS_FAILED:
            grouped_counts[bucket]["fail"] += 1

    return {
        "labels": labels,
        "success": [grouped_counts[label]["success"] for label in labels],
        "fail": [grouped_counts[label]["fail"] for label in labels],
        "granularity": "hour" if normalized_key == "24h" else "day",
        "granularity_label": granularity_label,
    }


def get_group_health_stats(session: Session, *, window_days: int | None = None) -> list[dict[str, Any]]:
    stmt = (
        select(
            DeviceGroup.name,
            func.count(BackupRecord.id).label("total"),
            func.sum(
                case((BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED, 1), else_=0)
            ).label("success_count"),
        )
        .join(Device, Device.group_id == DeviceGroup.id)
        .join(BackupRecord, BackupRecord.device_id == Device.id)
    )
    if window_days is not None:
        window_start = datetime.utcnow() - timedelta(days=max(1, int(window_days)))
        stmt = stmt.where(BackupRecord.started_at >= window_start)
    rows = session.exec(
        stmt.group_by(DeviceGroup.id, DeviceGroup.name)
        .having(func.count(BackupRecord.id) > 0)
        .order_by(
            (
                func.sum(
                    case((BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED, 1), else_=0)
                ) * 1.0
                / func.nullif(func.count(BackupRecord.id), 0)
            ).desc(),
            DeviceGroup.name,
        )
    ).all()

    return [
        {
            "name": group_name,
            "value": round((int(success_count or 0) / int(total or 1)) * 100, 1),
        }
        for group_name, total, success_count in rows
    ]


def _list_config_change_timestamps(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[datetime]:
    devices = session.exec(select(Device.id)).all()
    change_timestamps: list[datetime] = []

    for did in devices:
        prev = session.exec(
            select(BackupRecord)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
            .where(BackupRecord.started_at < start_at)
            .order_by(BackupRecord.started_at.desc())
            .limit(1)
        ).first()
        rows = session.exec(
            select(BackupRecord)
            .where(BackupRecord.device_id == did)
            .where(BackupRecord.status == task_state_service.BACKUP_RECORD_STATUS_SUCCEEDED)
            .where(BackupRecord.started_at >= start_at)
            .where(BackupRecord.started_at < end_at)
            .order_by(BackupRecord.started_at.asc())
        ).all()
        for row in rows:
            if prev and prev.config_text and row.config_text and prev.config_text != row.config_text:
                change_timestamps.append(row.started_at)
            prev = row

    return change_timestamps


def get_config_change_heatmap_stats(session: Session, *, window_key: str = "30d") -> dict[str, Any]:
    normalized_key = (window_key or "30d").strip().lower()
    if normalized_key not in {"24h", "7d", "30d"}:
        normalized_key = "30d"

    now = datetime.utcnow()
    if normalized_key == "24h":
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        start_at = current_hour - timedelta(hours=23)
        end_at = current_hour + timedelta(hours=1)
        x_labels = [(start_at + timedelta(hours=i)).strftime("%m-%d %H:00") for i in range(24)]
        y_labels = ["配置变更"]
        counts = {label: 0 for label in x_labels}
        for ts in _list_config_change_timestamps(session, start_at=start_at, end_at=end_at):
            key = ts.strftime("%m-%d %H:00")
            counts[key] = counts.get(key, 0) + 1
        data = [[idx, 0, counts[label]] for idx, label in enumerate(x_labels)]
        max_val = max((item[2] for item in data), default=0)
        return {
            "mode": "matrix",
            "x_labels": x_labels,
            "y_labels": y_labels,
            "data": data,
            "max": max_val,
            "range_label": "最近24小时",
        }

    if normalized_key == "7d":
        start_at = datetime(now.year, now.month, now.day) - timedelta(days=6)
        end_at = datetime(now.year, now.month, now.day) + timedelta(days=1)
        x_labels = [f"{hour:02d}:00" for hour in range(24)]
        y_labels = [(start_at + timedelta(days=i)).strftime("%m-%d") for i in range(7)]
        counts = {(day_label, hour_label): 0 for day_label in y_labels for hour_label in x_labels}
        for ts in _list_config_change_timestamps(session, start_at=start_at, end_at=end_at):
            day_label = ts.strftime("%m-%d")
            hour_label = ts.strftime("%H:00")
            counts[(day_label, hour_label)] = counts.get((day_label, hour_label), 0) + 1
        data = []
        max_val = 0
        for y_index, day_label in enumerate(y_labels):
            for x_index, hour_label in enumerate(x_labels):
                value = counts[(day_label, hour_label)]
                if value > max_val:
                    max_val = value
                data.append([x_index, y_index, value])
        return {
            "mode": "matrix",
            "x_labels": x_labels,
            "y_labels": y_labels,
            "data": data,
            "max": max_val,
            "range_label": "最近7天",
        }

    start_at = datetime(now.year, now.month, now.day) - timedelta(days=29)
    end_at = datetime(now.year, now.month, now.day) + timedelta(days=1)
    counts: dict[str, int] = {}
    for ts in _list_config_change_timestamps(session, start_at=start_at, end_at=end_at):
        key = ts.strftime("%Y-%m-%d")
        counts[key] = counts.get(key, 0) + 1

    data: list[list[Any]] = []
    max_val = 0
    for i in range(30):
        d = start_at + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        val = counts.get(key, 0)
        if val > max_val:
            max_val = val
        data.append([key, val])

    return {
        "mode": "calendar",
        "range": [start_at.strftime("%Y-%m-%d"), (end_at - timedelta(days=1)).strftime("%Y-%m-%d")],
        "data": data,
        "max": max_val,
        "range_label": "最近30天",
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
    return _flush_and_refresh(session, log)


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


def cleanup_old_audit_logs(session: Session, days: int) -> int:
    """清理指定天数之前的操作日志"""
    if days <= 0:
        return 0
    threshold = datetime.utcnow() - timedelta(days=days)
    stmt = delete(AuditLog).where(AuditLog.created_at < threshold)
    result = session.exec(stmt)
    _flush(session)
    return result.rowcount

def cleanup_old_login_logs(session: Session, days: int) -> int:
    """清理指定天数之前的登录日志"""
    if days <= 0:
        return 0
    threshold = datetime.utcnow() - timedelta(days=days)
    stmt = delete(LoginLog).where(LoginLog.created_at < threshold)
    result = session.exec(stmt)
    _flush(session)
    return result.rowcount


def cleanup_old_task_events(session: Session, days: int = 90) -> int:
    """清理指定天数之前的任务事件，默认仅保留最近 90 天。"""
    if days <= 0:
        return 0
    threshold = datetime.utcnow() - timedelta(days=days)
    stmt = delete(TaskEvent).where(TaskEvent.created_at < threshold)
    result = session.exec(stmt)
    _flush(session)
    return result.rowcount

def list_webshell_records(
    session: Session,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WebshellRecord]:
    stmt = select(WebshellRecord)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                WebshellRecord.username.like(like),
                WebshellRecord.device_name.like(like),
                WebshellRecord.device_host.like(like),
            )
        )
    stmt = stmt.order_by(WebshellRecord.started_at.desc()).offset(offset).limit(limit)
    return list(session.exec(stmt))

def count_webshell_records(
    session: Session,
    *,
    q: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(WebshellRecord)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                WebshellRecord.username.like(like),
                WebshellRecord.device_name.like(like),
                WebshellRecord.device_host.like(like),
            )
        )
    return int(session.exec(stmt).one())
