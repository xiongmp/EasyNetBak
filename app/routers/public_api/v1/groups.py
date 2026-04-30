from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlmodel import Session

from app import crud
from app.db import get_session
from app.routers.support import _require_api_permission, raise_api_error, raise_service_api_error
from app.schemas.api.resource import (
    GroupCreateSchema,
    GroupResponseSchema,
    GroupTreeNodeSchema,
    GroupUpdateSchema,
    OperationStatusSchema,
)
from app.services import resource_service


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])


@router.get("/groups", summary="获取所有设备分组", response_model=list[GroupResponseSchema], tags=["分组管理"])
def get_groups(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.view")
    return crud.list_groups(session)


@router.get("/groups/tree", summary="获取设备分组树", response_model=list[GroupTreeNodeSchema], tags=["分组管理"])
def get_group_tree(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.view")
    return resource_service.list_group_tree(session)


@router.get("/groups/{group_id}", summary="获取设备分组详情", response_model=GroupResponseSchema, tags=["分组管理"])
def get_group(request: Request, group_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.view")
    group = crud.get_group(session, group_id)
    if not group:
        raise_api_error(status_code=404, detail="Group not found", code="GROUP_NOT_FOUND")
    return group


@router.post("/groups", status_code=201, summary="新增设备分组", response_model=GroupResponseSchema, tags=["分组管理"])
def create_group_api(request: Request, data: GroupCreateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.create")
    try:
        return resource_service.create_group(
            session,
            resource_service.GroupCreateInput(name=data.name, parent_id=data.parent_id),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.put("/groups/{group_id}", summary="更新设备分组", response_model=GroupResponseSchema, tags=["分组管理"])
def update_group_api(request: Request, group_id: int, data: GroupUpdateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.update")
    try:
        return resource_service.update_group(
            session,
            group_id,
            resource_service.GroupUpdateInput(name=data.name, parent_id=data.parent_id),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.delete("/groups/{group_id}", summary="删除设备分组", response_model=OperationStatusSchema, tags=["分组管理"])
def delete_group_api(request: Request, group_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.delete")
    try:
        resource_service.delete_group(session, group_id)
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}
