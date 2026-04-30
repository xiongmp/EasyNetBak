from __future__ import annotations

from urllib.parse import urlencode

from app.schemas.pagination import PaginationData, PaginationParams


DEFAULT_LIMIT = 10
MAX_LIMIT = 100


def normalize_pagination_params(
    *,
    page: int,
    limit: int,
    limit_in_query: bool,
    default_limit: int = DEFAULT_LIMIT,
    max_limit: int = MAX_LIMIT,
) -> PaginationParams:
    safe_page = max(1, int(page or 1))
    safe_limit = max(1, min(int(limit or default_limit), int(max_limit or default_limit)))
    return PaginationParams(
        page=safe_page,
        limit=safe_limit,
        offset=(safe_page - 1) * safe_limit,
        limit_explicit=bool(limit_in_query),
    )


def build_pagination_data(*, page: int, limit: int, total: int) -> PaginationData:
    safe_limit = max(1, int(limit or DEFAULT_LIMIT))
    safe_total = max(0, int(total or 0))
    total_pages = max(1, (safe_total + safe_limit - 1) // safe_limit)
    return PaginationData(page=max(1, int(page or 1)), limit=safe_limit, total=safe_total, total_pages=total_pages)


def build_pagination_base(
    *,
    path: str,
    params: dict[str, object | None],
    page_param: str = "page",
    limit: int,
    default_limit: int = DEFAULT_LIMIT,
    limit_explicit: bool,
    limit_param: str = "limit",
) -> str:
    query: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        text = str(value)
        if text == "":
            query.append((key, ""))
            continue
        query.append((key, text))

    if limit_explicit or int(limit or default_limit) != int(default_limit):
        query.append((limit_param, str(limit)))

    query.append((page_param, ""))
    encoded = urlencode(query)
    suffix = f"?{encoded}" if encoded else "?"
    return f"{path}{suffix}"
