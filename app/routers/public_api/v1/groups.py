from fastapi import Depends, Request
from sqlmodel import Session

from app import crud
from app.db import get_session
from app.routers.public_api.v1.common import (
    limit_query,
    normalize_public_pagination,
    page_payload,
    page_query,
    public_api_router,
)
from app.routers.support import _require_api_permission, raise_api_error, raise_service_api_error
from app.schemas.api.resource import (
    GroupCreateSchema,
    GroupListResponseSchema,
    GroupResponseSchema,
    GroupTreeNodeSchema,
    GroupUpdateSchema,
    OperationStatusSchema,
)
from app.services import resource_service


router = public_api_router()


@router.get("/groups", summary="openapi.operation.get_groups", response_model=GroupListResponseSchema, tags=["分组管理"])
def get_groups(
    request: Request,
    page: int = page_query(),
    limit: int = limit_query(),
    session: Session = Depends(get_session),
):
    _require_api_permission(request, "groups.view")
    pagination = normalize_public_pagination(page=page, limit=limit)
    return page_payload(
        items=crud.list_groups(session, limit=pagination.limit, offset=pagination.offset),
        page=pagination.page,
        limit=pagination.limit,
        total=crud.count_groups(session),
    )


@router.get("/groups/tree", summary="openapi.operation.get_group_tree", response_model=list[GroupTreeNodeSchema], tags=["分组管理"])
def get_group_tree(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.view")
    return resource_service.list_group_tree(session)


@router.get("/groups/{group_id}", summary="openapi.operation.get_group", response_model=GroupResponseSchema, tags=["分组管理"])
def get_group(request: Request, group_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.view")
    group = crud.get_group(session, group_id)
    if not group:
        raise_api_error(status_code=404, detail="Group not found", code="GROUP_NOT_FOUND")
    return group


@router.post("/groups", status_code=201, summary="openapi.operation.create_group", response_model=GroupResponseSchema, tags=["分组管理"])
def create_group_api(request: Request, data: GroupCreateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.create")
    try:
        return resource_service.create_group(
            session,
            resource_service.GroupCreateInput(name=data.name, parent_id=data.parent_id),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.put("/groups/{group_id}", summary="openapi.operation.update_group", response_model=GroupResponseSchema, tags=["分组管理"])
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


@router.delete("/groups/{group_id}", summary="openapi.operation.delete_group", response_model=OperationStatusSchema, tags=["分组管理"])
def delete_group_api(request: Request, group_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "groups.delete")
    try:
        resource_service.delete_group(session, group_id)
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}
