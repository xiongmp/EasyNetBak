from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any, Mapping

from app.core.settings import settings
from app.i18n.validators import default_locale, normalize_locale, supported_locales


_LOCALES_DIR = Path(__file__).with_name("locales")


class CatalogValidationError(RuntimeError):
    pass


def _load_catalog_file(locale: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CatalogValidationError(f"Missing locale catalog: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogValidationError(f"Invalid locale catalog {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogValidationError(f"Locale catalog must be a JSON object: {path}")
    invalid = [key for key, value in data.items() if not isinstance(key, str) or not isinstance(value, str)]
    if invalid:
        raise CatalogValidationError(f"Locale catalog contains non-string entries: {path}")
    return dict(data)


@lru_cache(maxsize=8)
def get_messages(locale: str) -> dict[str, str]:
    normalized = normalize_locale(locale)
    return _load_catalog_file(normalized)


def _placeholders(message: str) -> set[str]:
    try:
        return {field_name for _, field_name, _, _ in Formatter().parse(message) if field_name}
    except ValueError as exc:
        raise CatalogValidationError(f"Invalid message format string: {message!r}") from exc


def validate_catalogs() -> None:
    """Fail fast when configured catalogs cannot provide a consistent contract."""
    locales = supported_locales()
    catalogs = {locale: _load_catalog_file(locale) for locale in locales}
    reference_locale = default_locale()
    reference = catalogs[reference_locale]
    problems: list[str] = []
    for locale, messages in catalogs.items():
        missing = sorted(set(reference) - set(messages))
        extra = sorted(set(messages) - set(reference))
        if missing:
            problems.append(f"{locale} missing keys: {', '.join(missing)}")
        if extra:
            problems.append(f"{locale} extra keys: {', '.join(extra)}")
        for key in sorted(set(reference) & set(messages)):
            expected = _placeholders(reference[key])
            actual = _placeholders(messages[key])
            if actual != expected:
                problems.append(
                    f"{locale} placeholder mismatch for {key}: expected {sorted(expected)}, got {sorted(actual)}"
                )
    if problems:
        raise CatalogValidationError("Locale catalog validation failed:\n- " + "\n- ".join(problems))


def has_key(key: str, locale: str | None = None) -> bool:
    normalized = normalize_locale(locale)
    return key in get_messages(normalized) or key in get_messages(default_locale())


def translate(
    locale: str | None,
    key: str,
    params: Mapping[str, Any] | None = None,
    fallback: str | None = None,
) -> str:
    normalized = normalize_locale(locale)
    message = get_messages(normalized).get(key)
    fallback_locale = default_locale()
    if message is None and normalized != fallback_locale:
        message = get_messages(fallback_locale).get(key)
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
