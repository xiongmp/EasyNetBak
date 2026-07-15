from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.i18n import get_current_locale, translate
from app.services.errors import ServiceError


@dataclass(slots=True, frozen=True)
class IntegrityRule:
    tokens: tuple[str, ...]
    message: str
    code: str
    status_code: int = 409
    context: dict[str, object] = field(default_factory=dict)
    message_key: str | None = None


_UNIQUE_ERROR_TOKENS = (
    "unique constraint",
    "duplicate key",
    "duplicate entry",
    "is not unique",
)


def raise_service_error_for_integrity(
    session: Session,
    exc: IntegrityError,
    *,
    rules: tuple[IntegrityRule, ...],
    fallback_message: str,
    fallback_code: str,
    fallback_message_key: str | None = None,
    fallback_status_code: int = 409,
) -> None:
    session.rollback()
    detail = str(getattr(exc, "orig", exc)).lower()

    for rule in rules:
        if any(token.lower() in detail for token in rule.tokens):
            raise ServiceError(
                (
                    translate(get_current_locale(), rule.message_key, fallback=rule.message)
                    if rule.message_key
                    else rule.message
                ),
                code=rule.code,
                status_code=rule.status_code,
                context=dict(rule.context),
            ) from exc

    if any(token in detail for token in _UNIQUE_ERROR_TOKENS):
        raise ServiceError(
            (
                translate(get_current_locale(), fallback_message_key, fallback=fallback_message)
                if fallback_message_key
                else fallback_message
            ),
            code=fallback_code,
            status_code=fallback_status_code,
        ) from exc

    raise exc
