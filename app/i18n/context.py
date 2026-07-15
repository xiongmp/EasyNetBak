from __future__ import annotations

from contextvars import ContextVar, Token

from app.i18n.validators import default_locale, normalize_locale


_current_locale: ContextVar[str] = ContextVar("nb_current_locale", default=default_locale())


def get_current_locale() -> str:
    return _current_locale.get()


def set_current_locale(locale: str | None) -> Token:
    return _current_locale.set(normalize_locale(locale))


def reset_current_locale(token: Token) -> None:
    _current_locale.reset(token)
