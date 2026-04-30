from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlmodel import Session

from app import crud
from app.db import get_session
from app.routers.support import _require_api_permission, raise_api_error, raise_service_api_error
from app.schemas.api.resource import (
    CredentialCreateSchema,
    CredentialResponseSchema,
    CredentialUpdateSchema,
    OperationStatusSchema,
)
from app.services import resource_service


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])


@router.get("/credentials", summary="获取所有登录凭据", response_model=list[CredentialResponseSchema], tags=["凭据管理"])
def get_credentials(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.view")
    return crud.list_credentials(session)


@router.get("/credentials/{credential_id}", summary="获取登录凭据详情", response_model=CredentialResponseSchema, tags=["凭据管理"])
def get_credential(request: Request, credential_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.view")
    cred = crud.get_credential(session, credential_id)
    if not cred:
        raise_api_error(status_code=404, detail="Credential not found", code="CREDENTIAL_NOT_FOUND")
    return cred


@router.post("/credentials", status_code=201, summary="新增登录凭据", response_model=CredentialResponseSchema, tags=["凭据管理"])
def create_credential_api(request: Request, data: CredentialCreateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.create")
    try:
        return resource_service.create_credential(
            session,
            resource_service.CredentialCreateInput(
                name=data.name,
                username=data.username,
                password=data.password,
                enable_password=data.enable_password,
                remarks=data.remarks,
            ),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.put("/credentials/{credential_id}", summary="更新登录凭据", response_model=CredentialResponseSchema, tags=["凭据管理"])
def update_credential_api(request: Request, credential_id: int, data: CredentialUpdateSchema, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.update")
    try:
        return resource_service.update_credential(
            session,
            credential_id,
            resource_service.CredentialUpdateInput(
                name=data.name,
                username=data.username,
                password=data.password,
                enable_password=data.enable_password,
                remarks=data.remarks,
            ),
        )
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)


@router.delete("/credentials/{credential_id}", summary="删除登录凭据", response_model=OperationStatusSchema, tags=["凭据管理"])
def delete_credential_api(request: Request, credential_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.delete")
    try:
        resource_service.delete_credential(session, credential_id)
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}
