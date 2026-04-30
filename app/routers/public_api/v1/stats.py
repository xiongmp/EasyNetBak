from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlmodel import Session

from app.db import get_session
from app.routers.support import _require_api_permission
from app.schemas.api.stats import SystemStatsResponseSchema
from app.services import stats_service


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key for third-party integrations")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(api_key_header)])


@router.get("/stats", summary="获取系统整体状态统计", response_model=SystemStatsResponseSchema, tags=["其它"])
def get_system_stats(request: Request, session: Session = Depends(get_session)):
    _require_api_permission(request, "dashboard.view")
    return stats_service.get_system_stats_payload(session)
