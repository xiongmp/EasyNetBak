from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote

from app.core.settings import settings


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.urlsafe_b64encode(salt).decode("utf-8").rstrip("="),
        base64.urlsafe_b64encode(dk).decode("utf-8").rstrip("="),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iters)
        salt = base64.urlsafe_b64decode(salt_b64 + "==")
        expected = base64.urlsafe_b64decode(dk_b64 + "==")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "==")


def create_session_token(*, user_id: int, ttl_seconds: int) -> str:
    if not settings.secret_key:
        raise RuntimeError("settings.secret_key is required for sessions")
    now = int(time.time())
    payload = {"v": 1, "uid": int(user_id), "exp": now + int(ttl_seconds)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def decode_session_token(token: str) -> dict[str, Any] | None:
    if not token or "." not in token or not settings.secret_key:
        return None
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64d(raw_b64)
        sig = _b64d(sig_b64)
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("v", 0)) != 1:
            return None
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            return None
        uid = int(payload.get("uid", 0))
        if uid <= 0:
            return None
        return payload
    except Exception:
        return None


def create_webshell_token(*, user_id: int, device_id: int, ttl_seconds: int) -> str:
    if not settings.secret_key:
        raise RuntimeError("settings.secret_key is required for sessions")
    now = int(time.time())
    payload = {
        "v": 1,
        "uid": int(user_id),
        "did": int(device_id),
        "exp": now + int(ttl_seconds),
        "t": "ws",
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def decode_webshell_token(token: str) -> dict[str, Any] | None:
    if not token or "." not in token or not settings.secret_key:
        return None
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64d(raw_b64)
        sig = _b64d(sig_b64)
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("v", 0)) != 1:
            return None
        if payload.get("t") != "ws":
            return None
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            return None
        uid = int(payload.get("uid", 0))
        did = int(payload.get("did", 0))
        if uid <= 0 or did <= 0:
            return None
        return payload
    except Exception:
        return None


def generate_mfa_secret(length: int = 20) -> str:
    return base64.b32encode(secrets.token_bytes(length)).decode("utf-8").replace("=", "")


def _mfa_key(secret: str) -> bytes:
    secret = (secret or "").strip().upper()
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret + padding, casefold=True)


def _mfa_code(secret: str, for_time: float, *, step: int = 30, digits: int = 6) -> str:
    counter = int(for_time // step)
    msg = counter.to_bytes(8, "big")
    key = _mfa_key(secret)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(value).zfill(digits)


def verify_mfa(secret: str, code: str, *, step: int = 30, digits: int = 6, window: int = 1) -> bool:
    raw = "".join(ch for ch in (code or "").strip() if ch.isdigit())
    if not raw:
        return False
    now = time.time()
    for offset in range(-window, window + 1):
        expected = _mfa_code(secret, now + (offset * step), step=step, digits=digits)
        if hmac.compare_digest(expected, raw):
            return True
    return False


def build_mfa_uri(*, secret: str, username: str, issuer: str, step: int = 30, digits: int = 6) -> str:
    label = f"{issuer}:{username}"
    return (
        f"otpauth://totp/{quote(label)}"
        f"?secret={quote(secret)}&issuer={quote(issuer)}&digits={digits}&period={step}"
    )


_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in (code or "").strip().upper() if ch.isalnum())


def _format_recovery_code(raw: str) -> str:
    return "-".join(raw[i : i + 5] for i in range(0, len(raw), 5))


def generate_recovery_codes(*, count: int = 5, length: int = 10) -> list[str]:
    codes: list[str] = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(length))
        codes.append(_format_recovery_code(raw))
    return codes


def hash_recovery_code(code: str) -> str:
    if not settings.secret_key:
        raise RuntimeError("settings.secret_key is required for recovery codes")
    normalized = normalize_recovery_code(code)
    return hmac.new(settings.secret_key.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
