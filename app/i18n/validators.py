from __future__ import annotations

import re

from app.core.settings import settings


_LANGUAGE_RANGE_RE = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")


def canonicalize_locale(value: str | None) -> str:
    """Return a consistently-cased BCP 47-style language tag."""
    parts = [part for part in (value or "").strip().replace("_", "-").split("-") if part]
    if not parts:
        return ""
    canonical = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            canonical.append(part.title())
        elif len(part) in {2, 3} and part.isalnum():
            canonical.append(part.upper())
        else:
            canonical.append(part.lower())
    return "-".join(canonical)


def supported_locales() -> tuple[str, ...]:
    values: list[str] = []
    for item in settings.supported_locales.split(","):
        locale = canonicalize_locale(item)
        if locale and locale not in values:
            values.append(locale)
    return tuple(values) or ("zh-CN", "en-US")


def default_locale() -> str:
    supported = supported_locales()
    configured = canonicalize_locale(settings.default_locale)
    return configured if configured in supported else supported[0]


def normalize_locale(value: str | None, *, fallback: str | None = None) -> str:
    supported = supported_locales()
    candidate = canonicalize_locale(value)
    if candidate in supported:
        return candidate

    language = candidate.partition("-")[0]
    if language:
        language_matches = [locale for locale in supported if locale.partition("-")[0] == language]
        if len(language_matches) == 1:
            return language_matches[0]
        configured_default = default_locale()
        if configured_default in language_matches:
            return configured_default

    return default_locale() if fallback is None else fallback


def validate_locale(value: str) -> str:
    normalized = normalize_locale(value, fallback="")
    if not normalized:
        raise ValueError(f"Unsupported locale: {value}")
    return normalized


def locale_from_accept_language(value: str | None) -> str | None:
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate((value or "").split(",")):
        segments = [segment.strip() for segment in part.split(";")]
        language = segments[0]
        quality = 1.0
        for option in segments[1:]:
            name, separator, raw_quality = option.partition("=")
            if separator != "=" or name.strip().lower() != "q":
                continue
            try:
                quality = float(raw_quality.strip())
            except ValueError:
                quality = 0.0
            break
        if quality <= 0 or quality > 1:
            continue
        if language == "*":
            normalized = default_locale()
        elif not _LANGUAGE_RANGE_RE.fullmatch(language):
            continue
        else:
            normalized = normalize_locale(language, fallback="")
        if normalized:
            candidates.append((quality, -index, normalized))
    return max(candidates, default=(0.0, 0, ""))[2] or None
