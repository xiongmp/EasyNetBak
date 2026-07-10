from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class ErrorDetailSchema(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetailSchema


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class PageResponse(BaseModel, Generic[T]):
    items: list[T]
    pagination: PaginationMeta


def build_pagination_meta(*, page: int, limit: int, total: int) -> PaginationMeta:
    safe_page = max(1, int(page or 1))
    safe_limit = max(1, int(limit or 1))
    safe_total = max(0, int(total or 0))
    total_pages = max(1, (safe_total + safe_limit - 1) // safe_limit)
    return PaginationMeta(
        page=safe_page,
        limit=safe_limit,
        total=safe_total,
        total_pages=total_pages,
        has_next=safe_page < total_pages,
        has_prev=safe_page > 1,
    )


def public_api_error_response(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }


PUBLIC_API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Bad request"},
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    403: {"model": ErrorResponse, "description": "Permission denied"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}
