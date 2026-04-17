from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select, func
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from uuid import UUID

from app import crud
from app.db import session_scope
from app.models import Device, DeviceGroup, Credential, BackupTemplate, BackupRecord
from app.routers.common import _require_permission, _current_user, get_user_allowed_group_ids
from app.platforms import normalize_platform_id, TELNET_DEVICE_TYPE_MAP, platforms_compatible
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])

# --- Schemas ---

class DeviceCreateSchema(BaseModel):
    name: str
    host: str
    port: int = 22
    login_method: str = "ssh"
    encoding: str = "utf-8"
    platform: str
    group_id: int = 0
    credential_id: int
    default_template_id: int = 0

class DeviceUpdateSchema(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    login_method: Optional[str] = None
    encoding: Optional[str] = None
    platform: Optional[str] = None
    group_id: Optional[int] = None
    credential_id: Optional[int] = None
    default_template_id: Optional[int] = None

class CredentialResponseSchema(BaseModel):
    id: int
    name: str
    username: str
    remarks: Optional[str] = None
    created_at: datetime

class GroupCreateSchema(BaseModel):
    name: str

class GroupUpdateSchema(BaseModel):
    name: str

class CredentialCreateSchema(BaseModel):
    name: str
    username: str
    password: Optional[str] = None
    enable_password: Optional[str] = None
    remarks: Optional[str] = None

class CredentialUpdateSchema(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enable_password: Optional[str] = None
    remarks: Optional[str] = None

# --- Category 1: Asset Sync API ---

@router.get("/devices", summary="获取设备列表", tags=["设备管理"])
def get_devices(
    request: Request,
    q: Optional[str] = None,
    group_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0
):
    user = _require_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        devices = crud.search_devices(
            session, q=q, platform=None, group_id=group_id, limit=limit, offset=offset, allowed_group_ids=allowed_ids
        )
        total = crud.count_devices(
            session, q=q, platform=None, group_id=group_id, allowed_group_ids=allowed_ids
        )
        return {"total": total, "items": devices}

@router.get("/devices/{device_id}", summary="获取设备详情", tags=["设备管理"])
def get_device(request: Request, device_id: int):
    user = _require_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            if not ((gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                raise HTTPException(status_code=403, detail="Permission denied for this device")
        return device

@router.post("/devices", status_code=201, summary="新增设备", tags=["设备管理"])
def create_device_api(request: Request, data: DeviceCreateSchema):
    _require_permission(request, "devices.create")
    with session_scope() as session:
        existing_name = session.exec(select(Device).where(Device.name == data.name.strip())).first()
        if existing_name:
            raise HTTPException(status_code=400, detail=f"Device name already exists: {data.name}")
        existing_host = session.exec(select(Device).where(Device.host == data.host.strip())).first()
        if existing_host:
            raise HTTPException(status_code=400, detail=f"Host already exists: {data.host}")
        
        cred = crud.get_credential(session, data.credential_id)
        if cred is None:
            raise HTTPException(status_code=400, detail="Credential not found")
            
        login_method = data.login_method.strip().lower()
        if login_method not in {"ssh", "telnet"}:
            login_method = "ssh"
            
        platform = data.platform.strip()
        base_platform = normalize_platform_id(platform)
        if login_method == "telnet":
            if base_platform not in TELNET_DEVICE_TYPE_MAP:
                raise HTTPException(status_code=400, detail="Telnet not supported for this platform")
            platform = TELNET_DEVICE_TYPE_MAP.get(base_platform, base_platform + "_telnet")
        else:
            platform = base_platform

        dtid = data.default_template_id if data.default_template_id else 0
        if dtid:
            tpl = crud.get_template(session, dtid)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, platform):
                raise HTTPException(status_code=400, detail="Template platform mismatch")

        device = Device(
            name=data.name.strip(),
            host=data.host.strip(),
            port=data.port,
            login_method=login_method,
            encoding=data.encoding.strip() or "utf-8",
            platform=platform,
            group_id=data.group_id or None,
            credential_id=data.credential_id,
            default_template_id=dtid or None,
        )
        crud.create_device(session, device=device)
        return device

@router.put("/devices/{device_id}", summary="更新设备", tags=["设备管理"])
def update_device_api(request: Request, device_id: int, data: DeviceUpdateSchema):
    user = _require_permission(request, "devices.update")
    allowed_ids = get_user_allowed_group_ids(user)
    
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
            
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            if not ((gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                raise HTTPException(status_code=403, detail="Permission denied for this device")
            
            if data.group_id is not None:
                target_gid = data.group_id if data.group_id else 0
                if not ((target_gid in allowed_set) or (target_gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                    raise HTTPException(status_code=403, detail="Permission denied to move to target group")

        if data.name is not None and data.name.strip() != device.name:
            existing = session.exec(select(Device).where(Device.name == data.name.strip())).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Device name already exists: {data.name}")
        if data.host is not None and data.host.strip() != device.host:
            existing = session.exec(select(Device).where(Device.host == data.host.strip())).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Host already exists: {data.host}")

        upd_name = data.name if data.name is not None else device.name
        upd_host = data.host if data.host is not None else device.host
        upd_port = data.port if data.port is not None else device.port
        upd_login_method = data.login_method if data.login_method is not None else device.login_method
        upd_encoding = data.encoding if data.encoding is not None else device.encoding
        upd_platform = data.platform if data.platform is not None else device.platform
        upd_group_id = data.group_id if data.group_id is not None else device.group_id
        upd_credential_id = data.credential_id if data.credential_id is not None else device.credential_id
        upd_default_template_id = data.default_template_id if data.default_template_id is not None else device.default_template_id

        # Validate template and cred
        if upd_credential_id:
            if crud.get_credential(session, upd_credential_id) is None:
                raise HTTPException(status_code=400, detail="Credential not found")
        if upd_default_template_id:
            tpl = crud.get_template(session, upd_default_template_id)
            if tpl is None:
                raise HTTPException(status_code=400, detail="Template not found")
            if not platforms_compatible(tpl.platform, normalize_platform_id(upd_platform)):
                raise HTTPException(status_code=400, detail="Template platform mismatch")

        updated = crud.update_device(
            session,
            device_id,
            name=upd_name,
            host=upd_host,
            port=upd_port,
            login_method=upd_login_method,
            encoding=upd_encoding,
            platform=upd_platform,
            group_id=upd_group_id,
            credential_id=upd_credential_id,
            default_template_id=upd_default_template_id
        )
        return updated

@router.delete("/devices/{device_id}", summary="删除设备", tags=["设备管理"])
def delete_device_api(request: Request, device_id: int):
    user = _require_permission(request, "devices.delete")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            if not ((gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                raise HTTPException(status_code=403, detail="Permission denied for this device")
        
        crud.delete_device(session, device_id)
        return {"status": "success"}

# --- Category 2: Basic Resource Dictionary API ---

@router.get("/groups", summary="获取所有设备分组", tags=["分组管理"])
def get_groups(request: Request):
    _require_permission(request, "resources.view")
    with session_scope() as session:
        groups = crud.list_groups(session)
        return groups

@router.get("/groups/{group_id}", summary="获取设备分组详情", tags=["分组管理"])
def get_group(request: Request, group_id: int):
    _require_permission(request, "resources.view")
    with session_scope() as session:
        group = crud.get_group(session, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        return group

@router.post("/groups", status_code=201, summary="新增设备分组", tags=["分组管理"])
def create_group_api(request: Request, data: GroupCreateSchema):
    _require_permission(request, "resources.create")
    with session_scope() as session:
        existing = session.exec(select(DeviceGroup).where(DeviceGroup.name == data.name.strip())).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Group name already exists: {data.name}")
        group = crud.create_group(session, name=data.name)
        return group

@router.put("/groups/{group_id}", summary="更新设备分组", tags=["分组管理"])
def update_group_api(request: Request, group_id: int, data: GroupUpdateSchema):
    _require_permission(request, "resources.update")
    with session_scope() as session:
        group = crud.get_group(session, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        if data.name.strip() != group.name:
            existing = session.exec(select(DeviceGroup).where(DeviceGroup.name == data.name.strip())).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Group name already exists: {data.name}")
        updated = crud.update_group(session, group_id, name=data.name)
        return updated

@router.delete("/groups/{group_id}", summary="删除设备分组", tags=["分组管理"])
def delete_group_api(request: Request, group_id: int):
    _require_permission(request, "resources.delete")
    with session_scope() as session:
        group = crud.get_group(session, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Check if group is in use
        usage_count = crud.group_usage_count(session, group_id)
        if usage_count > 0:
            raise HTTPException(status_code=400, detail=f"Group is in use by {usage_count} device(s)")
            
        crud.delete_group(session, group_id)
        return {"status": "success"}

@router.get("/credentials", summary="获取所有登录凭据", response_model=List[CredentialResponseSchema], tags=["凭据管理"])
def get_credentials(request: Request):
    _require_permission(request, "resources.view")
    with session_scope() as session:
        creds = crud.list_credentials(session)
        return creds

@router.get("/credentials/{credential_id}", summary="获取登录凭据详情", response_model=CredentialResponseSchema, tags=["凭据管理"])
def get_credential(request: Request, credential_id: int):
    _require_permission(request, "resources.view")
    with session_scope() as session:
        cred = crud.get_credential(session, credential_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")
        return cred

@router.post("/credentials", status_code=201, summary="新增登录凭据", response_model=CredentialResponseSchema, tags=["凭据管理"])
def create_credential_api(request: Request, data: CredentialCreateSchema):
    _require_permission(request, "resources.create")
    with session_scope() as session:
        existing = session.exec(select(Credential).where(Credential.name == data.name.strip())).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Credential name already exists: {data.name}")
        
        cred_obj = Credential(
            name=data.name.strip(),
            username=data.username.strip(),
            password=data.password,
            enable_password=data.enable_password,
            remarks=data.remarks
        )
        created = crud.create_credential(session, credential=cred_obj)
        return created

@router.put("/credentials/{credential_id}", summary="更新登录凭据", response_model=CredentialResponseSchema, tags=["凭据管理"])
def update_credential_api(request: Request, credential_id: int, data: CredentialUpdateSchema):
    _require_permission(request, "resources.update")
    with session_scope() as session:
        cred = crud.get_credential(session, credential_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")
            
        if data.name is not None and data.name.strip() != cred.name:
            existing = session.exec(select(Credential).where(Credential.name == data.name.strip())).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Credential name already exists: {data.name}")

        upd_name = data.name if data.name is not None else cred.name
        upd_username = data.username if data.username is not None else cred.username
        
        updated = crud.update_credential(
            session,
            credential_id,
            name=upd_name,
            username=upd_username,
            password=data.password,
            enable_password=data.enable_password,
            remarks=data.remarks if data.remarks is not None else cred.remarks
        )
        return updated

@router.delete("/credentials/{credential_id}", summary="删除登录凭据", tags=["凭据管理"])
def delete_credential_api(request: Request, credential_id: int):
    _require_permission(request, "resources.delete")
    with session_scope() as session:
        cred = crud.get_credential(session, credential_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")
        
        try:
            crud.delete_credential(session, credential_id)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
            
        return {"status": "success"}

@router.get("/templates", summary="获取所有自定义备份模板", tags=["备份管理"])
def get_templates(request: Request):
    _require_permission(request, "resources.view")
    with session_scope() as session:
        templates = crud.list_templates(session)
        return templates

# --- Category 3: Configuration & Backup Retrieval API ---

@router.get("/devices/{device_id}/backups", summary="获取设备备份历史", tags=["备份管理"])
def get_device_backups(request: Request, device_id: int, limit: int = 10, offset: int = 0):
    user = _require_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        if allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            if not ((gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                raise HTTPException(status_code=403, detail="Permission denied for this device")
        
        backups = crud.list_device_backups(session, device_id, limit=limit, offset=offset)
        total = crud.count_device_backups(session, device_id)
        return {"total": total, "items": backups}

@router.get("/backups/{backup_id}/content", summary="获取某次备份的具体配置内容", tags=["备份管理"])
def get_backup_content(request: Request, backup_id: UUID):
    user = _require_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        record = session.get(BackupRecord, backup_id)
        if not record:
            raise HTTPException(status_code=404, detail="Backup record not found")
        
        device = crud.get_device(session, record.device_id)
        if device and allowed_ids is not None:
            gid = device.group_id if device.group_id else 0
            allowed_set = set(allowed_ids)
            if not ((gid in allowed_set) or (gid == 0 and (-1 in allowed_set or 0 in allowed_set))):
                raise HTTPException(status_code=403, detail="Permission denied for this device")
                
        return {"config_text": record.config_text}

# --- Category 4: Status & Monitoring API ---

@router.get("/stats", summary="获取系统整体状态统计", tags=["其它"])
def get_system_stats(request: Request):
    _require_permission(request, "dashboard.view")
    with session_scope() as session:
        total_devices = crud.count_devices(session, q=None, platform=None, group_id=None)
        unreachable_devices = crud.count_devices(session, q=None, platform=None, group_id=None, reachability_status=False)
        
        # Count failures in last 24h
        now = datetime.utcnow()
        last_24h = datetime.fromtimestamp(now.timestamp() - 86400)
        stmt_failed_backups = select(func.count()).select_from(BackupRecord).where(
            BackupRecord.success == False,
            BackupRecord.started_at >= last_24h
        )
        failed_backups_24h = session.exec(stmt_failed_backups).one()
        
        return {
            "total_devices": total_devices,
            "unreachable_devices": unreachable_devices,
            "failed_backups_24h": failed_backups_24h
        }

@router.get("/devices/unreachable", summary="获取当前不可达的设备列表", tags=["其它"])
def get_unreachable_devices(request: Request, limit: int = 50, offset: int = 0):
    user = _require_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user)
    with session_scope() as session:
        devices = crud.search_devices(
            session, q=None, platform=None, group_id=None, reachability_status=False, limit=limit, offset=offset, allowed_group_ids=allowed_ids
        )
        total = crud.count_devices(
            session, q=None, platform=None, group_id=None, reachability_status=False, allowed_group_ids=allowed_ids
        )
        return {"total": total, "items": devices}
