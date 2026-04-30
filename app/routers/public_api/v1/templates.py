from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlmodel import Session

from app import crud
from app.db import get_session
from app.routers.support import _require_api_permission, raise_api_error, raise_service_api_error
from app.schemas.api.resource import (
    OperationStatusSchema,
    TemplateCreateSchema,
    TemplateResponseSchema,
    TemplateUpdateSchema,
)
from app.services import resource_service


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])


@router.get("/templates", summary="获取所有自定义备份模板", response_model=list[TemplateResponseSchema], tags=["备份管理"])
def get_templates(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "templates.view")
    return crud.list_templates(session)


@router.get("/templates/{template_id}", summary="获取备份模板详情", response_model=TemplateResponseSchema, tags=["备份管理"])
def get_template(request: Request, template_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "templates.view")
    template = crud.get_template(session, template_id)
    if not template:
        raise_api_error(status_code=404, detail="Template not found", code="TEMPLATE_NOT_FOUND")
    return template


@router.post("/templates", status_code=201, summary="新增备份模板", response_model=TemplateResponseSchema, tags=["备份管理"])
def create_template_api(request: Request, data: TemplateCreateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "templates.create")
    try:
        return resource_service.create_template(
            session,
            resource_service.TemplateCreateInput(
                name=data.name,
                platform=data.platform,
                commands=data.commands,
            ),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.put("/templates/{template_id}", summary="更新备份模板", response_model=TemplateResponseSchema, tags=["备份管理"])
def update_template_api(request: Request, template_id: int, data: TemplateUpdateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "templates.update")
    try:
        return resource_service.update_template(
            session,
            template_id,
            resource_service.TemplateUpdateInput(
                name=data.name,
                platform=data.platform,
                commands=data.commands,
            ),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.delete("/templates/{template_id}", summary="删除备份模板", response_model=OperationStatusSchema, tags=["备份管理"])
def delete_template_api(request: Request, template_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "templates.delete")
    try:
        resource_service.delete_template(session, template_id)
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}
