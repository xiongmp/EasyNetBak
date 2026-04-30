from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from app import crud
from app.core.settings import settings
from app.core.time import normalize_timezone_offset
from app.db import session_scope
from app.models import User
from app.services.apikey import hash_api_key


@dataclass(frozen=True, slots=True)
class RequestContext:
    user: User | None
    tz_offset: str
    tz_offset_minutes: int


def timezone_offset_to_minutes(offset: str | None) -> int:
    normalized = normalize_timezone_offset(offset, default=settings.timezone_offset)
    sign = -1 if normalized.startswith("-") else 1
    hh = int(normalized[1:3])
    mm = int(normalized[4:6])
    return sign * (hh * 60 + mm)


def build_request_context(*, user: User | None, timezone_offset: str | None) -> RequestContext:
    normalized = normalize_timezone_offset(timezone_offset, default=settings.timezone_offset)
    return RequestContext(
        user=user,
        tz_offset=normalized,
        tz_offset_minutes=timezone_offset_to_minutes(normalized),
    )


def build_default_request_context() -> RequestContext:
    return build_request_context(user=None, timezone_offset=settings.timezone_offset)


def apply_request_context(request: Request, context: RequestContext) -> None:
    request.state.user = context.user
    request.state.tz_offset = context.tz_offset
    request.state.tz_offset_minutes = context.tz_offset_minutes


def load_auth_context(*, user_id: int = 0, api_key_str: str | None = None) -> RequestContext:
    timezone_str: str | None = None
    user: User | None = None

    with session_scope() as session:
        timezone_str = crud.get_setting(session, key="timezone_offset")
        if api_key_str:
            key_hash = hash_api_key(api_key_str)
            api_key = crud.get_api_key_by_hash(session, key_hash)
            if api_key and api_key.is_active:
                if api_key.expires_at is None or api_key.expires_at > datetime.utcnow():
                    crud.update_api_key_last_used(session, api_key.id)
                    if api_key.created_by:
                        user = crud.get_user(session, api_key.created_by)
        elif user_id > 0:
            user = crud.get_user(session, user_id)

        if user:
            session.refresh(user)
            session.expunge(user)

    return build_request_context(user=user, timezone_offset=timezone_str)
