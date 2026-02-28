from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# To avoid circular imports, we don't import settings here for the default value.
# Callers should pass the default if needed, or we can handle it inside.
# But to match the original signature's behavior, we might need the default constant.
# We will define a fallback default here, but callers should prefer passing the configured value.

_TZ_OFFSET_RE = re.compile(r"^\s*([+-])?\s*(\d{1,2})(?::?(\d{2}))?\s*$")


def parse_timezone_offset_to_minutes(value: str | None) -> int | None:
    if value is None:
        return None
    m = _TZ_OFFSET_RE.match(value)
    if not m:
        return None
    sign = -1 if (m.group(1) or "+") == "-" else 1
    hours = int(m.group(2))
    minutes = int(m.group(3) or "0")
    if hours > 14 or minutes > 59:
        return None
    return sign * (hours * 60 + minutes)


def normalize_timezone_offset(value: str | None, *, default: str = "+08:00") -> str:
    minutes = parse_timezone_offset_to_minutes(value)
    if minutes is None:
        minutes = parse_timezone_offset_to_minutes(default) or 0
    sign = "+" if minutes >= 0 else "-"
    mins = abs(int(minutes))
    h = mins // 60
    m = mins % 60
    return f"{sign}{h:02d}:{m:02d}"


def apply_timezone_offset(value: datetime | None, offset_minutes: int) -> datetime | None:
    if value is None:
        return None
    offset_minutes = int(offset_minutes)
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone(timedelta(minutes=offset_minutes)))
    return value + timedelta(minutes=offset_minutes)
