from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app import crud
from app.models import ApiKey
from app.services.apikey import generate_api_key
from app.services.db_error_service import IntegrityRule, raise_service_error_for_integrity
from app.services import pagination_service


_API_KEY_INTEGRITY_RULES = (
    IntegrityRule(
        tokens=("api_key.prefix", "apikey.prefix", "prefix"),
        message="API key prefix conflict; generate a new key.",
        code="API_KEY_PREFIX_CONFLICT",
        message_key="error.api_key.prefix_conflict",
    ),
    IntegrityRule(
        tokens=("api_key.key_hash", "apikey.key_hash", "key_hash"),
        message="API key hash conflict; generate a new key.",
        code="API_KEY_HASH_CONFLICT",
        message_key="error.api_key.hash_conflict",
    ),
)


def create_api_key(
    session: Session,
    *,
    name: str,
    created_by: int | None,
    expires_in_days: int = 0,
) -> tuple[ApiKey, str]:
    plaintext_key, key_hash, prefix = generate_api_key()
    expires_at = None
    if int(expires_in_days or 0) > 0:
        expires_at = datetime.utcnow() + timedelta(days=int(expires_in_days))

    api_key = ApiKey(
        name=(name or "").strip(),
        key_hash=key_hash,
        prefix=prefix,
        is_active=True,
        scopes="all",
        created_by=created_by,
        expires_at=expires_at,
    )

    try:
        created = crud.create_api_key(session, api_key=api_key)
    except IntegrityError as exc:
        raise_service_error_for_integrity(
            session,
            exc,
            rules=_API_KEY_INTEGRITY_RULES,
            fallback_message="API key creation failed; try again.",
            fallback_code="API_KEY_CREATE_CONFLICT",
            fallback_message_key="error.api_key.create_conflict",
        )

    return created, plaintext_key


def get_api_keys_page_payload(
    session: Session,
    *,
    page: int,
    limit: int,
    limit_in_query: bool,
) -> dict[str, object]:
    params = pagination_service.normalize_pagination_params(
        page=page,
        limit=limit,
        limit_in_query=limit_in_query,
    )
    api_keys = crud.get_api_keys(session, skip=params.offset, limit=params.limit)
    total = crud.count_api_keys(session)
    pagination = pagination_service.build_pagination_data(
        page=params.page,
        limit=params.limit,
        total=total,
    )
    pagination_base = pagination_service.build_pagination_base(
        path="/api-keys",
        params={},
        limit=pagination.limit,
        limit_explicit=params.limit_explicit,
    )
    return {
        "api_keys": api_keys,
        "pagination": pagination.as_dict(),
        "pagination_base": pagination_base,
    }
