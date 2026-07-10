from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.security import APIKeyHeader

from app.schemas.api.common import PUBLIC_API_ERROR_RESPONSES, build_pagination_meta
from app.services import pagination_service


api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API Key for third-party integrations",
)


def public_api_router() -> APIRouter:
    return APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(api_key_header)],
        responses=PUBLIC_API_ERROR_RESPONSES,
    )


def page_query(default: int = 1):
    return Query(default, ge=1, description="Page number, starting from 1")


def limit_query(default: int = 50, maximum: int = 100):
    return Query(default, ge=1, le=maximum, description=f"Items per page, max {maximum}")


def normalize_public_pagination(
    *,
    page: int,
    limit: int,
    default_limit: int = 50,
    max_limit: int = 100,
):
    return pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=True,
        default_limit=default_limit,
        max_limit=max_limit,
    )


def page_payload(*, items: list[Any], page: int, limit: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "pagination": build_pagination_meta(page=page, limit=limit, total=total),
    }
