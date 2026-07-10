from __future__ import annotations

from app.core.settings import settings


LOCALE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "en": "en-US",
    "en-us": "en-US",
}


def supported_locales() -> tuple[str, ...]:
    values = tuple(x.strip() for x in settings.supported_locales.split(",") if x.strip())
    return values or ("zh-CN", "en-US")


def normalize_locale(value: str | None, *, fallback: str | None = None) -> str:
    raw = (value or "").strip()
    normalized = LOCALE_ALIASES.get(raw.lower(), raw)
    supported = supported_locales()
    if normalized in supported:
        return normalized
    default_locale = settings.default_locale if settings.default_locale in supported else supported[0]
    return default_locale if fallback is None else fallback


def validate_locale(value: str) -> str:
    normalized = normalize_locale(value, fallback="")
    if not normalized:
        raise ValueError(f"Unsupported locale: {value}")
    return normalized


def locale_from_accept_language(value: str | None) -> str | None:
    candidates: list[tuple[float, str]] = []
    for index, part in enumerate((value or "").split(",")):
        language, _, options = part.strip().partition(";")
        quality = 1.0 - index / 1000
        if options.strip().lower().startswith("q="):
            try:
                quality = float(options.strip()[2:])
            except ValueError:
                quality = 0.0
        normalized = normalize_locale(language, fallback="")
        if normalized:
            candidates.append((quality, normalized))
    return max(candidates, default=(0.0, ""))[1] or None
