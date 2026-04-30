from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlmodel import Session

from app.db import get_session
from app.routers.support import _require_api_permission, get_user_allowed_group_ids, raise_service_api_error
from app.schemas.api.backup import DeviceBackupsResponseSchema
from app.schemas.api.device import (
    DeviceCreateSchema,
    DeviceListResponseSchema,
    DeviceResponseSchema,
    DeviceUpdateSchema,
)
from app.schemas.api.resource import OperationStatusSchema
from app.services import backup_service, device_service


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])


@router.get("/devices", summary="获取设备列表", response_model=DeviceListResponseSchema, tags=["设备管理"])
def get_devices(
    request: Request,
    session: Session = Depends(get_session),
    q: str | None = None,
    group_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    user = _require_api_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    filters = device_service.normalize_list_filters(q=q, group_id=group_id)
    return device_service.list_devices_payload(
        session,
        filters=filters,
        limit=limit,
        offset=offset,
        allowed_group_ids=allowed_ids,
    )


@router.get("/devices/{device_id}", summary="获取设备详情", response_model=DeviceResponseSchema, tags=["设备管理"])
def get_device(request: Request, device_id: int, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        return device_service.get_device_detail(
            session,
            device_id=device_id,
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.post("/devices", status_code=201, summary="新增设备", response_model=DeviceResponseSchema, tags=["设备管理"])
def create_device_api(request: Request, data: DeviceCreateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "devices.create")
    try:
        return device_service.create_device(
            session,
            device_service.DeviceCreateInput(
                name=data.name,
                host=data.host,
                port=data.port,
                login_method=data.login_method,
                encoding=data.encoding,
                platform=data.platform,
                group_id=data.group_id,
                credential_id=data.credential_id,
                default_template_id=data.default_template_id,
            ),
        )
    except device_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.put("/devices/{device_id}", summary="更新设备", response_model=DeviceResponseSchema, tags=["设备管理"])
def update_device_api(request: Request, device_id: int, data: DeviceUpdateSchema, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "devices.update")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        return device_service.update_device(
            session,
            device_id=device_id,
            data=device_service.DeviceUpdateInput(
                name=data.name,
                host=data.host,
                port=data.port,
                login_method=data.login_method,
                encoding=data.encoding,
                platform=data.platform,
                group_id=data.group_id,
                credential_id=data.credential_id,
                default_template_id=data.default_template_id,
            ),
            allowed_group_ids=allowed_ids,
        )
    except device_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.delete("/devices/{device_id}", summary="删除设备", response_model=OperationStatusSchema, tags=["设备管理"])
def delete_device_api(request: Request, device_id: int, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "devices.delete")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        device_service.delete_device(session, device_id=device_id, allowed_group_ids=allowed_ids)
    except device_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}


@router.get("/devices/{device_id}/backups", summary="获取设备备份历史", response_model=DeviceBackupsResponseSchema, tags=["备份管理"])
def get_device_backups(request: Request, device_id: int, limit: int = 10, offset: int = 0, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "backups.view")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        device_service.get_device_detail(
            session,
            device_id=device_id,
            allowed_group_ids=allowed_ids,
        )
        page = max(1, (max(0, int(offset or 0)) // max(1, int(limit or 10))) + 1)
        payload = backup_service.list_device_backups_payload(
            session,
            device_id=device_id,
            page=page,
            limit=limit,
            offset_minutes=0,
        )
    except (device_service.ServiceError, backup_service.ServiceError) as exc:
        raise_service_api_error(exc)
    return {
        "total": payload["pagination"]["total"],
        "items": payload["backups"],
    }


@router.get("/devices/unreachable", summary="获取当前不可达设备列表", response_model=DeviceListResponseSchema, tags=["其它"])
def get_unreachable_devices(request: Request, limit: int = 50, offset: int = 0, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "devices.view")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    filters = device_service.normalize_list_filters(status="offline")
    return device_service.list_devices_payload(
        session,
        filters=filters,
        limit=limit,
        offset=offset,
        allowed_group_ids=allowed_ids,
    )
