from __future__ import annotations

from fastapi import Request

from app.core.settings import settings
from app.i18n.context import reset_current_locale, set_current_locale
from app.i18n.validators import locale_from_accept_language, normalize_locale


LOCALE_COOKIE = "nb_locale"
LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def set_locale_cookie(response, locale: str) -> None:
    response.set_cookie(
        LOCALE_COOKIE,
        normalize_locale(locale),
        max_age=LOCALE_COOKIE_MAX_AGE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def resolve_request_locale(request: Request, user=None) -> tuple[str, bool]:
    query_locale = request.query_params.get("lang")
    if query_locale:
        normalized = normalize_locale(query_locale, fallback="")
        if normalized:
            return normalized, user is None
    header_locale = locale_from_accept_language(request.headers.get("Accept-Language"))
    if request.url.path.startswith("/api/v1/") and header_locale:
        return header_locale, False
    user_locale = getattr(user, "locale", None) if user is not None else None
    if user_locale:
        normalized = normalize_locale(user_locale, fallback="")
        if normalized:
            return normalized, False
    cookie_locale = request.cookies.get(LOCALE_COOKIE)
    if cookie_locale:
        normalized = normalize_locale(cookie_locale, fallback="")
        if normalized:
            return normalized, False
    return normalize_locale(header_locale or settings.default_locale), False


def localize_response(request: Request, response, locale: str, *, persist_cookie: bool = False):
    """Apply locale metadata only to dynamic responses that can vary by language."""
    if request.url.path.startswith("/static/"):
        return response
    response.headers["Content-Language"] = locale
    response.headers.add_vary_header("Accept-Language")
    if not request.url.path.startswith("/api/v1/"):
        response.headers.add_vary_header("Cookie")
    if persist_cookie:
        set_locale_cookie(response, locale)
    return response


async def i18n_http_middleware(request: Request, call_next):
    locale = getattr(request.state, "locale", None)
    persist_cookie = bool(getattr(request.state, "persist_locale_cookie", False))
    if not locale:
        locale, persist_cookie = resolve_request_locale(request, user=getattr(request.state, "user", None))
        request.state.locale = locale
        request.state.persist_locale_cookie = persist_cookie
    token = set_current_locale(locale)
    try:
        response = await call_next(request)
    finally:
        reset_current_locale(token)
    return localize_response(
        request,
        response,
        getattr(request.state, "locale", locale),
        persist_cookie=persist_cookie,
    )
