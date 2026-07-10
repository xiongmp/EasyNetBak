from uuid import UUID

from fastapi import Depends, Request
from sqlmodel import Session

from app.db import get_session
from app.routers.public_api.v1.common import public_api_router
from app.routers.support import _require_api_permission, get_user_allowed_group_ids, raise_service_api_error
from app.schemas.api.backup import BackupContentResponseSchema
from app.services import backup_service


router = public_api_router()


@router.get("/backups/{backup_id}/content", summary="获取某次备份的具体配置内容", response_model=BackupContentResponseSchema, tags=["备份管理"])
def get_backup_content(request: Request, backup_id: UUID, session: Session = Depends(get_session)):
    user = _require_api_permission(request, "backups.view")
    allowed_ids = get_user_allowed_group_ids(user, session=session)
    try:
        detail = backup_service.get_backup_content(
            session,
            backup_id,
            allowed_group_ids=allowed_ids,
        )
    except backup_service.ServiceError as exc:
        raise_service_api_error(exc)
    return {"config_text": detail.record.config_text}
