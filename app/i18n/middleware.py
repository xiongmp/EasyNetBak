from __future__ import annotations

from fastapi import Request

from app.core.settings import settings
from app.i18n.context import reset_current_locale, set_current_locale
from app.i18n.validators import locale_from_accept_language, normalize_locale


LOCALE_COOKIE = "nb_locale"


def resolve_request_locale(request: Request, user=None) -> tuple[str, bool]:
    user_locale = getattr(user, "locale", None) if user is not None else None
    if user_locale:
        return normalize_locale(user_locale), False
    query_locale = request.query_params.get("lang")
    if query_locale:
        normalized = normalize_locale(query_locale, fallback="")
        if normalized:
            return normalized, True
    cookie_locale = request.cookies.get(LOCALE_COOKIE)
    if cookie_locale:
        return normalize_locale(cookie_locale), False
    header_locale = locale_from_accept_language(request.headers.get("Accept-Language"))
    return normalize_locale(header_locale or settings.default_locale), False


async def i18n_http_middleware(request: Request, call_next):
    locale, persist_cookie = resolve_request_locale(request, user=getattr(request.state, "user", None))
    request.state.locale = locale
    token = set_current_locale(locale)
    try:
        response = await call_next(request)
    finally:
        reset_current_locale(token)
    response.headers["Content-Language"] = getattr(request.state, "locale", locale)
    if persist_cookie:
        response.set_cookie(LOCALE_COOKIE, locale, path="/", samesite="lax")
    return response
