from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.core.settings import settings
from app.i18n.validators import normalize_locale


_LOCALES_DIR = Path(__file__).with_name("locales")


@lru_cache(maxsize=8)
def get_messages(locale: str) -> dict[str, str]:
    normalized = normalize_locale(locale)
    path = _LOCALES_DIR / f"{normalized}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {str(key): str(value) for key, value in data.items()}


def has_key(key: str, locale: str | None = None) -> bool:
    normalized = normalize_locale(locale)
    return key in get_messages(normalized) or key in get_messages(settings.default_locale)


def translate(
    locale: str | None,
    key: str,
    params: Mapping[str, Any] | None = None,
    fallback: str | None = None,
) -> str:
    normalized = normalize_locale(locale)
    message = get_messages(normalized).get(key)
    if message is None and normalized != settings.default_locale:
        message = get_messages(settings.default_locale).get(key)
    if message is None:
        message = fallback if fallback is not None else key
    if params:
        try:
            message = message.format_map(_SafeParams(params))
        except (ValueError, TypeError):
            pass
    return message


class _SafeParams(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
