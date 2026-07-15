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
    CredentialCreateSchema,
    CredentialListResponseSchema,
    CredentialResponseSchema,
    CredentialUpdateSchema,
    OperationStatusSchema,
)
from app.services import resource_service


router = public_api_router()


@router.get("/credentials", summary="openapi.operation.get_credentials", response_model=CredentialListResponseSchema, tags=["凭据管理"])
def get_credentials(
    request: Request,
    page: int = page_query(),
    limit: int = limit_query(),
    session: Session = Depends(get_session),
):
    _require_api_permission(request, "credentials.view")
    pagination = normalize_public_pagination(page=page, limit=limit)
    return page_payload(
        items=crud.list_credentials(session, limit=pagination.limit, offset=pagination.offset),
        page=pagination.page,
        limit=pagination.limit,
        total=crud.count_credentials(session),
    )


@router.get("/credentials/{credential_id}", summary="openapi.operation.get_credential", response_model=CredentialResponseSchema, tags=["凭据管理"])
def get_credential(request: Request, credential_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.view")
    cred = crud.get_credential(session, credential_id)
    if not cred:
        raise_api_error(status_code=404, detail="Credential not found", code="CREDENTIAL_NOT_FOUND")
    return cred


@router.post("/credentials", status_code=201, summary="openapi.operation.create_credential", response_model=CredentialResponseSchema, tags=["凭据管理"])
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


@router.put("/credentials/{credential_id}", summary="openapi.operation.update_credential", response_model=CredentialResponseSchema, tags=["凭据管理"])
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


@router.delete("/credentials/{credential_id}", summary="openapi.operation.delete_credential", response_model=OperationStatusSchema, tags=["凭据管理"])
def delete_credential_api(request: Request, credential_id: int, session: Session = Depends(get_session)):
    _require_api_permission(request, "credentials.delete")
    try:
        resource_service.delete_credential(session, credential_id)
    except resource_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"status": "success"}
