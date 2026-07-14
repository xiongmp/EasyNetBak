from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.i18n.catalog import get_messages
from app.i18n.validators import default_locale, normalize_locale


_APP_DIR = Path(__file__).resolve().parents[1]
_JS_KEY_PATTERN = re.compile(r"(?:window\.)?NB\.t\(\s*(['\"])([^'\"]+)\1")
_DATA_KEY_PATTERN = re.compile(r"data-(?:confirm|i18n)-key\s*=\s*(['\"])([^'\"]+)\1")


@lru_cache(maxsize=1)
def _javascript_message_keys() -> frozenset[str]:
    """Collect the explicit catalog contract required by browser code."""
    keys: set[str] = set()
    for root, pattern in (
        (_APP_DIR / "static" / "js", _JS_KEY_PATTERN),
        (_APP_DIR / "templates", _DATA_KEY_PATTERN),
    ):
        for path in root.rglob("*"):
            if path.suffix not in {".js", ".html"} or path.name.endswith(".min.js"):
                continue
            source = path.read_text(encoding="utf-8-sig")
            keys.update(match.group(2) for match in pattern.finditer(source))
    return frozenset(keys)


def javascript_messages(locale: str | None) -> dict[str, str]:
    normalized = normalize_locale(locale)
    keys = _javascript_message_keys()
    messages = {key: value for key, value in get_messages(default_locale()).items() if key in keys}
    if normalized != default_locale():
        messages.update({key: value for key, value in get_messages(normalized).items() if key in keys})
    return messages
