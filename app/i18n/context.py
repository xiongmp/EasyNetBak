from __future__ import annotations

from contextvars import ContextVar, Token

from app.core.settings import settings
from app.i18n.validators import normalize_locale


_current_locale: ContextVar[str] = ContextVar("nb_current_locale", default=settings.default_locale)


def get_current_locale() -> str:
    return _current_locale.get()


def set_current_locale(locale: str | None) -> Token:
    return _current_locale.set(normalize_locale(locale))


def reset_current_locale(token: Token) -> None:
    _current_locale.reset(token)
