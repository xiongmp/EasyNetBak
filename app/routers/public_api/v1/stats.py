from fastapi import Depends, Request
from sqlmodel import Session

from app.db import get_session
from app.routers.public_api.v1.common import public_api_router
from app.routers.support import _require_api_permission
from app.schemas.api.stats import SystemStatsResponseSchema
from app.services import stats_service


router = public_api_router()


@router.get("/stats", summary="获取系统整体状态统计", response_model=SystemStatsResponseSchema, tags=["其它"])
def get_system_stats(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "dashboard.view")
    return stats_service.get_system_stats_payload(session)
