from __future__ import annotations

from app.i18n.catalog import get_messages
from app.i18n.validators import default_locale, normalize_locale
from app.i18n.legacy import LEGACY_EN


def javascript_messages(locale: str | None) -> dict[str, str]:
    normalized = normalize_locale(locale)
    messages = dict(get_messages(default_locale()))
    if normalized != default_locale():
        messages.update(get_messages(normalized))
    return messages


def legacy_javascript_messages(locale: str | None) -> dict[str, str]:
    return dict(LEGACY_EN) if normalize_locale(locale) == "en-US" else {}
