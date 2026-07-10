from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from fastapi.openapi.utils import get_openapi

from app.i18n.catalog import has_key, translate
from app.i18n.validators import normalize_locale
from app.i18n.legacy import translate_legacy_text


_TRANSLATABLE_FIELDS = {"title", "description", "summary"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _contains_han(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _operation_label(operation: dict[str, Any], method: str, path: str) -> str:
    operation_id = str(operation.get("operationId") or "").strip()
    if operation_id:
        words = operation_id.rsplit("_", 1)[0].replace("_", " ").strip()
        if words:
            return words[:1].upper() + words[1:]
    return f"{method.upper()} {path}"


def _localize_legacy_openapi(schema: dict[str, Any], locale: str) -> None:
    if locale != "en-US":
        return
    tag_names = {
        "设备管理": "Devices",
        "分组管理": "Groups",
        "凭据管理": "Credentials",
        "备份管理": "Backups",
        "其它": "Other",
    }
    for tag in schema.get("tags") or []:
        name = str(tag.get("name") or "")
        if _contains_han(name):
            tag["name"] = tag_names.get(name, "Other")
        description = str(tag.get("description") or "")
        if _contains_han(description):
            tag["description"] = f"API endpoints for {tag['name'].lower()}"
    for path, path_item in (schema.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            label = _operation_label(operation, method, path)
            if _contains_han(str(operation.get("summary") or "")):
                operation["summary"] = label
            if _contains_han(str(operation.get("description") or "")):
                operation["description"] = f"{label}."
            operation["tags"] = [tag_names.get(tag, "Other") if _contains_han(tag) else tag for tag in operation.get("tags") or []]


def _translate_schema_value(value: Any, locale: str, field: str | None = None) -> Any:
    if isinstance(value, dict):
        return {key: _translate_schema_value(item, locale, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_translate_schema_value(item, locale, field) for item in value]
    if isinstance(value, str) and field in _TRANSLATABLE_FIELDS and has_key(value, locale):
        return translate(locale, value, fallback=value)
    if isinstance(value, str) and field in _TRANSLATABLE_FIELDS and locale == "en-US" and _contains_han(value):
        localized = translate_legacy_text(value, locale)
        return localized if not _contains_han(localized) else f"API {field}"
    return value


@lru_cache(maxsize=8)
def _cached_schema(app_id: int, locale: str, title: str, version: str, description: str, routes_key: int):
    # The app instance is supplied through the temporary registry below so the cache key stays hashable.
    app = _APP_REGISTRY[app_id]
    schema = get_openapi(title=title, version=version, description=description, routes=app.routes)
    return _translate_schema_value(schema, locale)


_APP_REGISTRY: dict[int, Any] = {}


def build_openapi_schema(app, locale: str) -> dict[str, Any]:
    normalized = normalize_locale(locale)
    _APP_REGISTRY[id(app)] = app
    schema = deepcopy(_cached_schema(id(app), normalized, app.title, app.version, app.description or "", len(app.routes)))
    schema.setdefault("info", {})["title"] = translate(normalized, "openapi.title", fallback=app.title)
    schema["info"]["description"] = translate(normalized, "openapi.description", fallback=app.description or "")
    _localize_legacy_openapi(schema, normalized)
    return schema
