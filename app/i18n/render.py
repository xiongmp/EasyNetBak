from __future__ import annotations

from app.i18n.catalog import get_messages
from app.i18n.validators import normalize_locale
from app.i18n.legacy import LEGACY_EN


def javascript_messages(locale: str | None) -> dict[str, str]:
    return dict(get_messages(normalize_locale(locale)))


def legacy_javascript_messages(locale: str | None) -> dict[str, str]:
    return dict(LEGACY_EN) if normalize_locale(locale) == "en-US" else {}
