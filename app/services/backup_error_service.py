from __future__ import annotations

import re

from app.i18n import get_current_locale, has_key, translate
from app.i18n.validators import normalize_locale


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TECHNICAL_DETAIL_RE = re.compile(r"\s*(\(.+\))\s*$", re.DOTALL)


def localize_backup_error_message(
    error_message: str | None,
    failure_type: str | None = None,
    *,
    locale: str | None = None,
) -> str:
    """Localize a stored backup error without changing its diagnostic payload."""
    raw = str(error_message or "").strip()
    if not raw:
        return ""

    normalized_locale = normalize_locale(locale or get_current_locale())
    if normalized_locale != "en-US" or not _CJK_RE.search(raw):
        return raw

    normalized_failure_type = str(failure_type or "UNKNOWN").strip().upper() or "UNKNOWN"
    key = f"error.backup.failure.{normalized_failure_type}"
    if has_key(key, normalized_locale):
        summary = translate(normalized_locale, key)
        detail_match = _TECHNICAL_DETAIL_RE.search(raw)
        if detail_match:
            detail = detail_match.group(1).strip()
            if detail and not _CJK_RE.search(detail):
                return f"{summary} {detail}"
        return summary

    detail_match = _TECHNICAL_DETAIL_RE.search(raw)
    detail = detail_match.group(1).strip() if detail_match else ""
    fallback = translate(normalized_locale, "error.backup.failure.UNKNOWN", fallback="Backup failed")
    return f"{fallback} {detail}".strip() if detail and not _CJK_RE.search(detail) else fallback
