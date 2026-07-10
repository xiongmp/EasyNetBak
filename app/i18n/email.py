from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.i18n.catalog import translate
from app.i18n.validators import normalize_locale


_EMAIL_DIR = Path(__file__).resolve().parents[1] / "templates" / "emails"


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_EMAIL_DIR)),
        autoescape=select_autoescape(("html", "xml")),
        enable_async=False,
    )


def render_email_template(template_name: str, *, locale: str, context: dict[str, Any]) -> str:
    normalized = normalize_locale(locale)
    template = _environment().get_template(template_name)
    return template.render(
        **context,
        locale=normalized,
        _=lambda key, params=None, fallback=None: translate(normalized, key, params, fallback),
    )
