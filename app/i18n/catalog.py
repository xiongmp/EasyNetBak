from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from string import Formatter
from types import MappingProxyType
from typing import Any, Mapping

from app.core.settings import settings
from app.i18n.validators import default_locale, normalize_locale, supported_locales


_LOCALES_DIR = Path(__file__).with_name("locales")
logger = logging.getLogger(__name__)
_PLACEHOLDER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")


class CatalogValidationError(RuntimeError):
    pass


@lru_cache(maxsize=1024)
def _warn_missing_key(locale: str, key: str) -> None:
    logger.warning("Missing i18n key %r for locale %s", key, locale)


def _load_catalog_file(locale: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{locale}.json"
    try:
        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            duplicates: list[str] = []
            for key, value in pairs:
                if key in result:
                    duplicates.append(key)
                result[key] = value
            if duplicates:
                raise CatalogValidationError(
                    f"Locale catalog contains duplicate keys {path}: {', '.join(sorted(set(duplicates)))}"
                )
            return result

        data = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=reject_duplicate_keys,
        )
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


@lru_cache(maxsize=None)
def get_messages(locale: str) -> Mapping[str, str]:
    normalized = normalize_locale(locale)
    return MappingProxyType(_load_catalog_file(normalized))


def _placeholders(message: str) -> set[str]:
    try:
        fields: set[str] = set()
        for _, field_name, format_spec, conversion in Formatter().parse(message):
            if not field_name:
                continue
            if format_spec or conversion:
                raise CatalogValidationError(
                    f"Message placeholders must not use format specs or conversions: {message!r}"
                )
            if not _PLACEHOLDER_NAME_RE.fullmatch(field_name):
                raise CatalogValidationError(
                    f"Message placeholders must be simple identifiers: {message!r}"
                )
            fields.add(field_name)
        return fields
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
        html_keys = sorted(key for key, value in messages.items() if _HTML_TAG_RE.search(value))
        if html_keys:
            problems.append(f"{locale} HTML is not allowed in messages: {', '.join(html_keys)}")
        legacy_html_keys = sorted(key for key in messages if key.endswith("_html"))
        if legacy_html_keys:
            problems.append(f"{locale} legacy HTML key names: {', '.join(legacy_html_keys)}")
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


def message_placeholders(locale: str | None, key: str) -> set[str]:
    normalized = normalize_locale(locale)
    message = get_messages(normalized).get(key) or get_messages(default_locale()).get(key)
    return _placeholders(message) if message is not None else set()


def translate(
    locale: str | None,
    key: str,
    params: Mapping[str, Any] | None = None,
    fallback: str | None = None,
    *,
    strict: bool | None = None,
) -> str:
    normalized = normalize_locale(locale)
    message = get_messages(normalized).get(key)
    fallback_locale = default_locale()
    if message is None and normalized != fallback_locale:
        message = get_messages(fallback_locale).get(key)
    if message is None:
        message = fallback if fallback is not None else key
        _warn_missing_key(normalized, key)
    expected_params = _placeholders(message)
    strict_mode = settings.i18n_strict if strict is None else strict
    supplied_params = set(params or {})
    if strict_mode and not expected_params.issubset(supplied_params):
        missing = sorted(expected_params - supplied_params)
        raise CatalogValidationError(
            f"Invalid parameters for i18n key {key!r}: missing={missing}"
        )
    if params:
        try:
            message = message.format_map(_SafeParams(params))
        except (ValueError, TypeError) as exc:
            logger.warning("Failed to interpolate i18n key %r: %s", key, exc)
    return message


def translate_plural(
    locale: str | None,
    key: str,
    count: int | float,
    params: Mapping[str, Any] | None = None,
    fallback: str | None = None,
    *,
    strict: bool | None = None,
) -> str:
    """Translate a ``key.<plural-category>`` message using locale-aware rules."""
    normalized = normalize_locale(locale)
    category = _plural_category(normalized.partition("-")[0], count)
    values = dict(params or {})
    values.setdefault("count", count)
    selected_key = f"{key}.{category}"
    if not has_key(selected_key, normalized):
        selected_key = f"{key}.other"
    return translate(normalized, selected_key, values, fallback, strict=strict)


def _plural_category(language: str, count: int | float) -> str:
    value = float(count)
    integer = int(value)
    if language in {"zh", "ja", "ko", "th", "vi"}:
        return "other"
    if language == "fr":
        return "one" if value in {0, 1} else "other"
    if language == "ar":
        if value == 0:
            return "zero"
        if value == 1:
            return "one"
        if value == 2:
            return "two"
        if value.is_integer() and integer % 100 in range(3, 11):
            return "few"
        if value.is_integer() and integer % 100 in range(11, 100):
            return "many"
        return "other"
    return "one" if value == 1 else "other"


class _SafeParams(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
