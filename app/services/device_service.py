from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import crud
from app.models import Device
from app.platforms import TELNET_DEVICE_TYPE_MAP, normalize_platform_id, platforms_compatible
from app.schemas.inputs import DeviceCreateInput, DeviceUpdateInput
from app.services import pagination_service
from app.services.db_error_service import IntegrityRule, raise_service_error_for_integrity


_SUPPORTED_DEVICE_ENCODINGS = {"utf-8", "gb18030", "gbk", "gb2312"}


from app.services.errors import ServiceError


_DEVICE_INTEGRITY_RULES = (
    IntegrityRule(
        tokens=("device.name", "uq_device_name"),
        message="Device name already exists",
        code="DEVICE_NAME_EXISTS",
    ),
    IntegrityRule(
        tokens=("device.host, device.port", "uq_device_host_port"),
        message="Device host and port already exist",
        code="DEVICE_HOST_EXISTS",
    ),
)


@dataclass(slots=True)
class NormalizedDevicePayload:
    name: str
    host: str
    port: int
    login_method: str
    encoding: str
    platform: str
    group_id: int | None
    credential_id: int | None
    default_template_id: int | None


@dataclass(slots=True)
class DeviceListFilters:
    q: str | None
    login_method: str | None
    platform: str | None
    group_id: int | None
    reachability_status: bool | None
    status_raw: str


@dataclass(slots=True)
class DeviceImportResult:
    created: int
    updated: int
    skipped: int
    report: list[dict[str, str]]
    affected_device_ids: list[int]
    log_entries: list[dict[str, Any]]
    mode: str
    match_by: str


def _normalize_device_encoding(value: str | None) -> str:
    encoding = (value or "utf-8").strip().lower()
    if not encoding:
        return "utf-8"
    return encoding if encoding in _SUPPORTED_DEVICE_ENCODINGS else "utf-8"


def normalize_list_filters(
    *,
    q: str | None = None,
    login_method: str | None = None,
    platform: str | None = None,
    group_id: int | str | None = None,
    status: str | None = None,
) -> DeviceListFilters:
    normalized_q = (q or "").strip() or None
    login_method_raw = (login_method or "").strip().lower()
    normalized_login_method = login_method_raw if login_method_raw in {"ssh", "telnet"} else None
    normalized_platform = (platform or "").strip() or None
    if normalized_platform and normalized_login_method:
        base_platform = normalize_platform_id(normalized_platform)
        if normalized_login_method == "telnet":
            normalized_platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, normalized_platform)
        else:
            normalized_platform = base_platform

    group_raw = str(group_id or "").strip()
    normalized_group_id = int(group_raw) if group_raw.isdigit() and int(group_raw) > 0 else None

    status_raw = (status or "").strip().lower()
    reachability_status = None
    if status_raw == "online":
        reachability_status = True
    elif status_raw == "offline":
        reachability_status = False

    return DeviceListFilters(
        q=normalized_q,
        login_method=normalized_login_method,
        platform=normalized_platform,
        group_id=normalized_group_id,
        reachability_status=reachability_status,
        status_raw=status_raw,
    )


def _group_access_allowed(group_id: int, allowed_group_ids: list[int] | None) -> bool:
    if allowed_group_ids is None:
        return True
    allowed_set = set(allowed_group_ids)
    return group_id in allowed_set or (group_id == 0 and (-1 in allowed_set or 0 in allowed_set))


def validate_target_group_access(target_group_id: int | None, allowed_group_ids: list[int] | None) -> None:
    target_gid = int(target_group_id or 0)
    if not _group_access_allowed(target_gid, allowed_group_ids):
        raise ServiceError(
            "Permission denied to move to target group",
            code="DEVICE_TARGET_GROUP_FORBIDDEN",
            status_code=403,
            context={"group_id": target_gid},
        )


def validate_device_access(device: Device, allowed_group_ids: list[int] | None, *, action: str = "access") -> None:
    group_id = int(device.group_id or 0)
    if _group_access_allowed(group_id, allowed_group_ids):
        return

    detail = {
        "view": "Permission denied for this device",
        "update": "Permission denied for this device",
        "delete": "Permission denied for this device",
    }.get(action, "Permission denied for this device")

    raise ServiceError(
        detail,
        code="DEVICE_ACCESS_FORBIDDEN",
        status_code=403,
        context={"device_id": int(device.id or 0), "group_id": group_id},
    )


def _normalize_login_method(value: str | None) -> str:
    login_method = (value or "ssh").strip().lower()
    return login_method if login_method in {"ssh", "telnet"} else "ssh"


def _normalize_platform(*, platform: str | None, login_method: str) -> str:
    base_platform = normalize_platform_id((platform or "").strip())
    if login_method == "telnet":
        if base_platform not in TELNET_DEVICE_TYPE_MAP:
            raise ServiceError(
                "Telnet not supported for this platform",
                code="DEVICE_TELNET_PLATFORM_UNSUPPORTED",
                status_code=400,
            )
        return TELNET_DEVICE_TYPE_MAP.get(base_platform, base_platform + "_telnet")
    return base_platform


def _validate_credential(session: Session, credential_id: int | None, *, required: bool = False) -> int | None:
    if credential_id:
        if crud.get_credential(session, int(credential_id)) is None:
            raise ServiceError(
                "Credential not found",
                code="DEVICE_CREDENTIAL_NOT_FOUND",
                status_code=400,
            )
        return int(credential_id)
    if required:
        raise ServiceError(
            "Credential not found",
            code="DEVICE_CREDENTIAL_NOT_FOUND",
            status_code=400,
        )
    return credential_id


def _validate_template(session: Session, template_id: int | None, *, platform: str) -> int | None:
    if template_id:
        template = crud.get_template(session, int(template_id))
        if template is None:
            raise ServiceError(
                "Template not found",
                code="DEVICE_TEMPLATE_NOT_FOUND",
                status_code=400,
            )
        if not platforms_compatible(template.platform, platform):
            raise ServiceError(
                "Template platform mismatch",
                code="DEVICE_TEMPLATE_PLATFORM_MISMATCH",
                status_code=400,
            )
        return int(template_id)
    return template_id


def _ensure_unique_name(session: Session, name: str, *, exclude_device_id: int | None = None) -> None:
    stmt = select(Device).where(Device.name == name)
    if exclude_device_id is not None:
        stmt = stmt.where(Device.id != exclude_device_id)
    existing = session.exec(stmt).first()
    if existing:
        raise ServiceError(
            f"Device name already exists: {name}",
            code="DEVICE_NAME_EXISTS",
            status_code=400,
            context={"name": name},
        )


def _ensure_unique_host_port(
    session: Session,
    host: str,
    port: int,
    *,
    exclude_device_id: int | None = None,
) -> None:
    stmt = select(Device).where(Device.host == host, Device.port == port)
    if exclude_device_id is not None:
        stmt = stmt.where(Device.id != exclude_device_id)
    existing = session.exec(stmt).first()
    if existing:
        raise ServiceError(
            f"Host and port already exist: {host}:{port}",
            code="DEVICE_HOST_EXISTS",
            status_code=400,
            context={"host": host, "port": int(port)},
        )


def _normalize_create_payload(session: Session, data: DeviceCreateInput) -> NormalizedDevicePayload:
    normalized_name = (data.name or "").strip()
    normalized_host = (data.host or "").strip()
    normalized_port = int(data.port)
    _ensure_unique_name(session, normalized_name)
    _ensure_unique_host_port(session, normalized_host, normalized_port)

    login_method = _normalize_login_method(data.login_method)
    encoding = _normalize_device_encoding(data.encoding)
    platform = _normalize_platform(platform=data.platform, login_method=login_method)
    credential_id = _validate_credential(session, int(data.credential_id), required=True)
    default_template_id = _validate_template(
        session,
        int(data.default_template_id) if data.default_template_id else 0,
        platform=platform,
    )

    return NormalizedDevicePayload(
        name=normalized_name,
        host=normalized_host,
        port=normalized_port,
        login_method=login_method,
        encoding=encoding,
        platform=platform,
        group_id=int(data.group_id) or None,
        credential_id=credential_id,
        default_template_id=default_template_id or None,
    )


def _normalize_update_payload(
    session: Session,
    *,
    device_id: int,
    current_device: Device,
    data: DeviceUpdateInput,
) -> NormalizedDevicePayload:
    normalized_name = (data.name if data.name is not None else current_device.name).strip()
    normalized_host = (data.host if data.host is not None else current_device.host).strip()
    normalized_port = int(data.port if data.port is not None else current_device.port)
    _ensure_unique_name(session, normalized_name, exclude_device_id=device_id)
    _ensure_unique_host_port(
        session,
        normalized_host,
        normalized_port,
        exclude_device_id=device_id,
    )

    login_method = _normalize_login_method(data.login_method if data.login_method is not None else current_device.login_method)
    encoding = _normalize_device_encoding(data.encoding if data.encoding is not None else current_device.encoding)
    raw_platform = data.platform if data.platform is not None else current_device.platform
    platform = _normalize_platform(platform=raw_platform, login_method=login_method)
    group_id = data.group_id if data.group_id is not None else current_device.group_id
    credential_id = data.credential_id if data.credential_id is not None else current_device.credential_id
    default_template_id = (
        data.default_template_id
        if data.default_template_id is not None
        else current_device.default_template_id
    )

    credential_id = _validate_credential(
        session,
        int(credential_id) if credential_id else credential_id,
        required=True,
    )
    default_template_id = _validate_template(
        session,
        int(default_template_id) if default_template_id else default_template_id,
        platform=platform,
    )

    return NormalizedDevicePayload(
        name=normalized_name,
        host=normalized_host,
        port=normalized_port,
        login_method=login_method,
        encoding=encoding,
        platform=platform,
        group_id=int(group_id) if group_id else None,
        credential_id=credential_id,
        default_template_id=default_template_id or None,
    )


def create_device(session: Session, data: DeviceCreateInput) -> Device:
    payload = _normalize_create_payload(session, data)
    device = Device(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        login_method=payload.login_method,
        encoding=payload.encoding,
        platform=payload.platform,
        group_id=payload.group_id,
        credential_id=payload.credential_id,
        default_template_id=payload.default_template_id,
    )
    try:
        return crud.create_device(session, device=device)
    except IntegrityError as exc:
        raise_service_error_for_integrity(
            session,
            exc,
            rules=_DEVICE_INTEGRITY_RULES,
            fallback_message="Device already exists",
            fallback_code="DEVICE_CONFLICT",
        )


def update_device(
    session: Session,
    *,
    device_id: int,
    data: DeviceUpdateInput,
    allowed_group_ids: list[int] | None = None,
) -> Device:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError("Device not found", code="DEVICE_NOT_FOUND", status_code=404)

    validate_device_access(device, allowed_group_ids, action="update")
    payload = _normalize_update_payload(session, device_id=device_id, current_device=device, data=data)
    validate_target_group_access(payload.group_id, allowed_group_ids)

    try:
        updated = crud.update_device(
            session,
            device_id,
            name=payload.name,
            host=payload.host,
            port=payload.port,
            login_method=payload.login_method,
            encoding=payload.encoding,
            platform=payload.platform,
            group_id=payload.group_id,
            credential_id=payload.credential_id,
            default_template_id=payload.default_template_id,
        )
    except IntegrityError as exc:
        raise_service_error_for_integrity(
            session,
            exc,
            rules=_DEVICE_INTEGRITY_RULES,
            fallback_message="Device already exists",
            fallback_code="DEVICE_CONFLICT",
        )
    if updated is None:
        raise ServiceError("Device not found", code="DEVICE_NOT_FOUND", status_code=404)
    return updated


def delete_device(session: Session, *, device_id: int, allowed_group_ids: list[int] | None = None) -> str:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError("Device not found", code="DEVICE_NOT_FOUND", status_code=404)

    validate_device_access(device, allowed_group_ids, action="delete")
    if crud.has_active_backups_for_device(session, device_id):
        raise ServiceError(
            "Device has active backup tasks",
            code="DEVICE_DELETE_ACTIVE_BACKUPS",
            status_code=409,
            context={"device_id": int(device_id)},
        )
    device_name = device.name or f"ID: {device_id}"
    crud.delete_device(session, device_id)
    return device_name


def bulk_delete_devices(
    session: Session,
    *,
    device_ids: list[int],
    allowed_group_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    devices_to_delete: list[Device] = []
    for device_id in device_ids:
        device = crud.get_device(session, device_id)
        if device is None:
            continue

        validate_device_access(device, allowed_group_ids, action="delete")
        if crud.has_active_backups_for_device(session, int(device_id)):
            raise ServiceError(
                "One or more devices have active backup tasks",
                code="DEVICE_BULK_DELETE_ACTIVE_BACKUPS",
                status_code=409,
                context={"device_id": int(device_id)},
            )
        devices_to_delete.append(device)

    deleted: list[dict[str, Any]] = []
    for device in devices_to_delete:
        if device.id is None:
            continue
        deleted.append({"device_id": int(device.id), "name": device.name})
        crud.delete_device(session, int(device.id), commit=False)
    return deleted


def bulk_update_devices(
    session: Session,
    *,
    device_ids: list[int],
    field: str,
    value: str,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    valid_fields = {"group_id", "platform", "login_method", "credential_id", "encoding"}
    if field not in valid_fields:
        raise ServiceError("无效的修改字段", code="DEVICE_BULK_INVALID_FIELD")

    new_value: Any = value
    if field == "group_id":
        try:
            group_id = int(value)
            new_value = group_id if group_id > 0 else None
        except ValueError:
            new_value = None
    elif field == "credential_id":
        try:
            new_value = int(value)
        except ValueError as exc:
            raise ServiceError("无效的凭据ID", code="DEVICE_BULK_INVALID_CREDENTIAL") from exc
        if not crud.get_credential(session, new_value):
            raise ServiceError("指定的凭据不存在", code="DEVICE_BULK_CREDENTIAL_NOT_FOUND")
    elif field == "login_method":
        if value not in {"ssh", "telnet"}:
            raise ServiceError("无效的登录方式", code="DEVICE_BULK_INVALID_LOGIN_METHOD")
    elif field == "platform":
        if not value:
            raise ServiceError("平台类型不能为空", code="DEVICE_BULK_PLATFORM_REQUIRED")
    elif field == "encoding":
        new_value = _normalize_device_encoding(value)

    count = 0
    updated_ids: list[int] = []
    log_entries: list[dict[str, Any]] = []

    for device_id in device_ids:
        device = crud.get_device(session, device_id)
        if device is None:
            continue
        if allowed_group_ids is not None:
            try:
                validate_device_access(device, allowed_group_ids, action="update")
            except ServiceError:
                continue

        old_value = getattr(device, field)
        target_value = new_value
        updated = False
        message = ""

        if field == "group_id":
            if old_value != target_value:
                device.group_id = target_value
                message = f"Bulk Update {field}: {old_value} -> {new_value}"
                updated = True
        elif field == "platform":
            if old_value != target_value:
                device.platform = target_value
                message = f"Bulk Update {field}: {old_value} -> {new_value}"
                updated = True
        elif field == "login_method":
            if old_value != target_value:
                device.login_method = target_value
                msg_suffix = ""
                if target_value == "telnet" and device.port == 22:
                    device.port = 23
                    msg_suffix = " (Port: 22 -> 23)"
                elif target_value == "ssh" and device.port == 23:
                    device.port = 22
                    msg_suffix = " (Port: 23 -> 22)"
                message = f"Bulk Update {field}: {old_value} -> {new_value}{msg_suffix}"
                updated = True
        elif field == "credential_id":
            if old_value != target_value:
                device.credential_id = target_value
                message = f"Bulk Update {field}: {old_value} -> {new_value}"
                updated = True
        elif field == "encoding":
            if old_value != target_value:
                device.encoding = target_value
                message = f"Bulk Update {field}: {old_value} -> {new_value}"
                updated = True

        if updated:
            count += 1
            updated_ids.append(int(device_id))
            log_entries.append({"device_id": int(device_id), "message": message})

    return {
        "count": count,
        "updated_ids": updated_ids,
        "log_entries": log_entries,
    }


def get_devices_page_payload(
    session: Session,
    *,
    filters: DeviceListFilters,
    page: int = 1,
    page_size: int = 10,
    include_limit_param: bool = False,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=page_size,
        limit_in_query=include_limit_param,
        default_limit=10,
        max_limit=100,
    )

    total = crud.count_devices(
        session,
        q=filters.q,
        login_method=filters.login_method,
        platform=filters.platform,
        group_id=filters.group_id,
        reachability_status=filters.reachability_status,
        allowed_group_ids=allowed_group_ids,
    )
    devices = crud.search_devices(
        session,
        q=filters.q,
        login_method=filters.login_method,
        platform=filters.platform,
        group_id=filters.group_id,
        reachability_status=filters.reachability_status,
        limit=params.limit,
        offset=params.offset,
        allowed_group_ids=allowed_group_ids,
    )
    templates = crud.list_templates(session)
    credentials = crud.list_credentials(session)
    groups = crud.list_groups(session)
    
    # 扁平化树结构
    from app.services import resource_service
    tree = resource_service.list_group_tree(session)
    flat_groups = []
    def _flatten(nodes):
        for node in nodes:
            flat_groups.append(node)
            if node.get("children"):
                _flatten(node["children"])
    _flatten(tree)

    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/devices",
        params={
            "q": filters.q or None,
            "login_method": filters.login_method or None,
            "platform": filters.platform or None,
            "group_id": filters.group_id or None,
            "status": filters.status_raw or None,
        },
        limit=pagination.limit,
        default_limit=10,
        limit_explicit=params.limit_explicit,
    )

    # 预计算全路径以供前端直接显示
    group_map = {group.id: group for group in groups if group.id}
    group_paths = {}
    for group in groups:
        path_parts = []
        current = group
        while current:
            path_parts.insert(0, current.name)
            current = group_map.get(current.parent_id) if current.parent_id else None
        group_paths[group.id] = " / ".join(path_parts)

    return {
        "devices": devices,
        "templates": templates,
        "credentials": credentials,
        "groups": flat_groups,
        "group_map": group_map,
        "group_paths": group_paths,
        "credential_map": {credential.id: credential for credential in credentials if credential.id},
        "filters": {
            "q": filters.q or "",
            "login_method": filters.login_method or "",
            "platform": filters.platform or "",
            "group_id": filters.group_id or 0,
            "status": filters.status_raw,
        },
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
    }


def get_devices_export_payload(
    session: Session,
    *,
    filters: DeviceListFilters,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    devices = crud.search_devices(
        session,
        q=filters.q,
        login_method=filters.login_method,
        platform=filters.platform,
        group_id=filters.group_id,
        reachability_status=filters.reachability_status,
        limit=100000,
        offset=0,
        allowed_group_ids=allowed_group_ids,
    )
    groups = {group.id: group for group in crud.list_groups(session) if group.id}
    credentials = {credential.id: credential for credential in crud.list_credentials(session) if credential.id}
    templates = {template.id: template for template in crud.list_templates(session) if template.id}
    return {
        "devices": devices,
        "groups": groups,
        "credentials": credentials,
        "templates": templates,
    }


def list_devices_payload(
    session: Session,
    *,
    filters: DeviceListFilters,
    limit: int = 50,
    offset: int = 0,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(int(limit or 50), 500))
    normalized_offset = max(0, int(offset or 0))
    total = crud.count_devices(
        session,
        q=filters.q,
        login_method=filters.login_method,
        platform=filters.platform,
        group_id=filters.group_id,
        reachability_status=filters.reachability_status,
        allowed_group_ids=allowed_group_ids,
    )
    items = crud.search_devices(
        session,
        q=filters.q,
        login_method=filters.login_method,
        platform=filters.platform,
        group_id=filters.group_id,
        reachability_status=filters.reachability_status,
        limit=normalized_limit,
        offset=normalized_offset,
        allowed_group_ids=allowed_group_ids,
    )
    return {"total": total, "items": items}


def get_device_detail(
    session: Session,
    *,
    device_id: int,
    allowed_group_ids: list[int] | None = None,
) -> Device:
    device = crud.get_device(session, device_id)
    if device is None:
        raise ServiceError(
            "Device not found",
            code="DEVICE_NOT_FOUND",
            status_code=404,
            context={"device_id": device_id},
        )
    validate_device_access(device, allowed_group_ids, action="view")
    return device


def get_device_detail_page_payload(
    session: Session,
    *,
    device_id: int,
    page: int = 1,
    page_size: int = 10,
    include_limit_param: bool = False,
    include_backups: bool = True,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    device = get_device_detail(
        session,
        device_id=device_id,
        allowed_group_ids=allowed_group_ids,
    )
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=page_size,
        limit_in_query=include_limit_param,
        default_limit=10,
        max_limit=200,
    )

    if include_backups:
        total_backups = crud.count_device_backups(session, device_id)
        backups = crud.list_device_backups(session, device_id, limit=params.limit, offset=params.offset)
    else:
        total_backups = 0
        backups = []
    templates = [
        template
        for template in crud.list_templates(session)
        if platforms_compatible(template.platform, device.platform)
    ]
    groups_list = crud.list_groups(session)
    if allowed_group_ids is not None:
        allowed_set = set(allowed_group_ids)
        groups_list = [group for group in groups_list if group.id in allowed_set]
        
    from app.services import resource_service
    tree = resource_service.list_group_tree(session)
    flat_groups = []
    def _flatten(nodes):
        for node in nodes:
            if allowed_group_ids is None or node["id"] in allowed_group_ids:
                flat_groups.append(node)
            if node.get("children"):
                _flatten(node["children"])
    _flatten(tree)

    credentials = {credential.id: credential for credential in crud.list_credentials(session) if credential.id}
    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total_backups,
    )
    pagination_base = pagination_service.build_pagination_base(
        path=f"/devices/{device_id}",
        params={},
        limit=pagination.limit,
        default_limit=10,
        limit_explicit=params.limit_explicit,
    )

    # 预计算全路径以供前端直接显示
    group_map = {group.id: group for group in groups_list if group.id}
    group_paths = {}
    for group in groups_list:
        path_parts = []
        current = group
        while current:
            path_parts.insert(0, current.name)
            current = group_map.get(current.parent_id) if current.parent_id else None
        group_paths[group.id] = " / ".join(path_parts)

    return {
        "device": device,
        "backups": backups,
        "templates": templates,
        "groups": flat_groups,
        "group_map": group_map,
        "group_paths": group_paths,
        "credentials": credentials,
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
    }


def get_webshell_page_payload(
    session: Session,
    *,
    device_id: int,
    allowed_group_ids: list[int] | None = None,
) -> dict[str, Any]:
    device = get_device_detail(
        session,
        device_id=device_id,
        allowed_group_ids=allowed_group_ids,
    )
    credential = crud.get_credential(session, device.credential_id) if device.credential_id else None
    all_devices = crud.search_devices(
        session,
        q=None,
        platform=None,
        group_id=None,
        limit=100000,
        offset=0,
        allowed_group_ids=allowed_group_ids,
    )
    groups = crud.list_groups(session)
    return {
        "device": device,
        "credential": credential,
        "all_devices": all_devices,
        "groups": groups,
    }


def resolve_reachability_device_ids(
    session: Session,
    *,
    requested_ids: list[int],
    raw_device_ids: str,
    filters: DeviceListFilters,
    allowed_group_ids: list[int] | None = None,
) -> list[int]:
    device_ids = list(requested_ids)
    if not device_ids and not raw_device_ids:
        devices = crud.search_devices(
            session,
            q=filters.q,
            login_method=filters.login_method,
            platform=filters.platform,
            group_id=filters.group_id,
            reachability_status=filters.reachability_status,
            limit=100000,
            offset=0,
            allowed_group_ids=allowed_group_ids,
        )
        device_ids = [int(device.id) for device in devices if device.id]

    if device_ids and allowed_group_ids is not None:
        valid_ids: list[int] = []
        for device in crud.get_devices_subset(session, device_ids):
            try:
                validate_device_access(device, allowed_group_ids, action="update")
            except ServiceError:
                continue
            if device.id:
                valid_ids.append(int(device.id))
        device_ids = valid_ids

    return device_ids


def import_devices_from_csv(
    session: Session,
    *,
    csv_text: str,
    mode: str,
    match_by: str,
) -> DeviceImportResult:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"name", "host", "port", "platform", "credential_name"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ServiceError("CSV缺少必要列", code="DEVICE_IMPORT_INVALID_COLUMNS")

    normalized_match_by = (match_by or "host_port").strip()
    if normalized_match_by not in {"host_port", "name"}:
        normalized_match_by = "host_port"

    created = 0
    updated = 0
    skipped = 0
    report: list[dict[str, str]] = []
    affected_device_ids: list[int] = []
    log_entries: list[dict[str, Any]] = []

    group_by_name = {group.name: int(group.id) for group in crud.list_groups(session) if group.id}
    cred_by_name = {credential.name: int(credential.id) for credential in crud.list_credentials(session) if credential.id}
    tpl_by_name = {
        (tpl.name or "").strip(): int(tpl.id)
        for tpl in crud.list_templates(session)
        if tpl.id and (tpl.name or "").strip()
    }

    for idx, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        host = (row.get("host") or "").strip()
        port_raw = (row.get("port") or "").strip()
        platform = (row.get("platform") or "").strip()
        login_method = (row.get("login_method") or "").strip().lower()
        encoding = _normalize_device_encoding(row.get("encoding"))
        group_name = (row.get("group_name") or "").strip()
        credential_name = (row.get("credential_name") or "").strip()
        default_template_name = (row.get("default_template_name") or "").strip()

        if not name or not host or not port_raw.isdigit() or not platform or not credential_name:
            skipped += 1
            report.append(
                {
                    "row": str(idx),
                    "action": "skip",
                    "name": name,
                    "host": host,
                    "message": "字段缺失或端口非法",
                }
            )
            continue

        if login_method not in {"ssh", "telnet"}:
            login_method = "telnet" if platform.endswith("_telnet") else "ssh"
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            if base_platform not in TELNET_DEVICE_TYPE_MAP:
                skipped += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": "Telnet 不支持该平台类型",
                    }
                )
                continue
            platform = TELNET_DEVICE_TYPE_MAP[base_platform]
        else:
            platform = base_platform

        credential_id = cred_by_name.get(credential_name)
        if not credential_id:
            skipped += 1
            report.append(
                {
                    "row": str(idx),
                    "action": "skip",
                    "name": name,
                    "host": host,
                    "message": "未找到匹配的 credential_name",
                }
            )
            continue

        default_template_id = None
        if default_template_name:
            template_id = tpl_by_name.get(default_template_name)
            if not template_id:
                skipped += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": "未找到匹配的 default_template_name",
                    }
                )
                continue
            try:
                default_template_id = _validate_template(session, int(template_id), platform=platform)
            except ServiceError as exc:
                msg = "备份模板无效"
                if exc.code == "DEVICE_TEMPLATE_PLATFORM_MISMATCH":
                    msg = "备份模板与设备类型不匹配"
                skipped += 1
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": msg,
                    }
                )
                continue

        group_id_val = None
        if group_name:
            group_id_val = group_by_name.get(group_name)
            if not group_id_val:
                group = crud.create_group(session, name=group_name)
                group_id_val = int(group.id) if group.id else None
                if group_id_val:
                    group_by_name[group_name] = group_id_val

        if mode == "insert":
            duplicated_name = session.exec(select(Device).where(Device.name == name)).first()
            duplicated_host = session.exec(select(Device).where(Device.host == host, Device.port == int(port_raw))).first()
            if duplicated_name or duplicated_host:
                skipped += 1
                duplicate_message = "重复：设备名称或管理地址(IP+端口)已存在"
                if duplicated_name and duplicated_host:
                    duplicate_message = "重复：设备名称和管理地址(IP+端口)已存在"
                elif duplicated_name:
                    duplicate_message = "重复：设备名称已存在"
                elif duplicated_host:
                    duplicate_message = "重复：管理地址(IP+端口)已存在"
                report.append(
                    {
                        "row": str(idx),
                        "action": "skip",
                        "name": name,
                        "host": host,
                        "message": duplicate_message,
                    }
                )
                continue

        existing = None
        if mode == "upsert":
            if normalized_match_by == "host_port":
                existing = session.exec(select(Device).where(Device.host == host, Device.port == int(port_raw))).first()
            else:
                existing = session.exec(select(Device).where(Device.name == name)).first()

        if existing:
            existing.name = name
            existing.host = host
            existing.port = int(port_raw)
            existing.platform = platform
            existing.login_method = login_method
            existing.encoding = encoding
            existing.group_id = group_id_val
            existing.credential_id = credential_id
            existing.default_template_id = default_template_id
            session.add(existing)
            updated += 1
            if existing.id:
                affected_device_ids.append(int(existing.id))
                log_entries.append(
                    {
                        "action": "UPDATE_DEVICE",
                        "resource_type": "device",
                        "resource_id": int(existing.id),
                        "detail": f"Name: {name}, Host: {host} (Import)",
                    }
                )
            report.append(
                {
                    "row": str(idx),
                    "action": "update",
                    "name": name,
                    "host": host,
                    "message": "已更新",
                }
            )
            continue

        device = Device(
            name=name,
            host=host,
            port=int(port_raw),
            login_method=login_method,
            encoding=encoding,
            platform=platform,
            group_id=group_id_val,
            credential_id=credential_id,
            default_template_id=default_template_id,
        )
        crud.create_device(session, device=device)
        session.flush()
        if device.id:
            affected_device_ids.append(int(device.id))
            log_entries.append(
                {
                    "action": "CREATE_DEVICE",
                    "resource_type": "device",
                    "resource_id": int(device.id),
                    "detail": f"Name: {name}, Host: {host} (Import)",
                }
            )
        created += 1
        report.append(
            {
                "row": str(idx),
                "action": "create",
                "name": name,
                "host": host,
                "message": "已创建",
            }
        )

    return DeviceImportResult(
        created=created,
        updated=updated,
        skipped=skipped,
        report=report,
        affected_device_ids=affected_device_ids,
        log_entries=log_entries,
        mode=mode,
        match_by=normalized_match_by,
    )
