from __future__ import annotations

import base64
import hashlib
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import settings


_PREFIX: Final[str] = "enc:v1:"


def _fernet() -> Fernet | None:
    if not settings.secret_key:
        return None
    key_bytes = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    f = _fernet()
    if f is None:
        raise RuntimeError("settings.secret_key is required for credential encryption")
    token = f.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not value.startswith(_PREFIX):
        return value
    f = _fernet()
    if f is None:
        return None
    token = value[len(_PREFIX) :]
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
