from __future__ import annotations

import io
import logging
from datetime import datetime
from ftplib import FTP, all_errors

from sqlmodel import Session

from app import crud
from app.core.settings import settings
from app.core.time import apply_timezone_offset, parse_timezone_offset_to_minutes
from app.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _safe_device_name(name: str) -> str:
    return "".join([c if c.isalnum() else "_" for c in (name or "")]) or "device"


def _parse_int(value: str | None, default: int, min_val: int, max_val: int) -> int:
    try:
        val = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    if val < min_val:
        return min_val
    if val > max_val:
        return max_val
    return val


def _ftp_connect(host: str, port: int, username: str, password: str, timeout: int, passive: bool) -> FTP:
    encodings = ["utf-8", "gbk", "latin-1"]
    last_exc: Exception | None = None
    for enc in encodings:
        ftp = FTP()
        ftp.encoding = enc
        try:
            ftp.connect(host=host, port=port, timeout=timeout)
            ftp.login(user=username, passwd=password)
            ftp.set_pasv(passive)
            return ftp
        except UnicodeDecodeError as exc:
            last_exc = exc
            try:
                ftp.close()
            except Exception:
                pass
            continue
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("FTP connection failed")


def _ensure_dir(ftp: FTP, segments: list[str]) -> None:
    for seg in segments:
        if not seg:
            continue
        try:
            ftp.cwd(seg)
        except all_errors:
            ftp.mkd(seg)
            ftp.cwd(seg)


def upload_backup_to_ftp(session: Session, device_name: str, host: str, config_text: str, finished_at: datetime) -> bool:
    ftp_enabled = crud.get_setting(session, key="ftp_enabled") == "1"
    if not ftp_enabled:
        return False

    ftp_host = (crud.get_setting(session, key="ftp_host") or "").strip()
    ftp_port = crud.get_setting(session, key="ftp_port") or "21"
    ftp_username = (crud.get_setting(session, key="ftp_username") or "").strip()
    ftp_password = decrypt_secret(crud.get_setting(session, key="ftp_password")) or ""
    ftp_base_dir = (crud.get_setting(session, key="ftp_base_dir") or "").strip()
    ftp_passive = crud.get_setting(session, key="ftp_passive") or "1"
    ftp_timeout = crud.get_setting(session, key="ftp_timeout") or "15"

    if not ftp_host:
        logger.error("FTP configuration is incomplete: missing host")
        return False

    username = ftp_username or "anonymous"
    password = ftp_password or ""
    port = _parse_int(ftp_port, 21, 1, 65535)
    timeout = _parse_int(ftp_timeout, 15, 1, 300)
    passive = ftp_passive in {"1", "on", "true", "yes"}

    tz_str = crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    offset_minutes = parse_timezone_offset_to_minutes(tz_str) or 0
    local_dt = apply_timezone_offset(finished_at, offset_minutes) or finished_at

    date_str = local_dt.strftime("%Y-%m-%d")
    time_str = local_dt.strftime("%H%M%S")
    safe_name = _safe_device_name(device_name)
    filename = f"{safe_name}_{host}_{time_str}.txt"

    base_clean = ftp_base_dir.strip().strip("/")
    segments: list[str] = []
    if base_clean:
        segments.extend([s for s in base_clean.split("/") if s])
    segments.append(date_str)

    ftp: FTP | None = None
    try:
        ftp = _ftp_connect(ftp_host, port, username, password, timeout, passive)
        _ensure_dir(ftp, segments)
        data = io.BytesIO(config_text.encode("utf-8"))
        ftp.storbinary(f"STOR {filename}", data)
        return True
    except Exception as exc:
        logger.error(f"Failed to upload backup to FTP: {exc}")
        return False
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass


def test_ftp_connection(
    host: str,
    port: str,
    username: str,
    password: str,
    base_dir: str,
    passive: str,
    timeout: str,
) -> tuple[bool, str]:
    ftp_host = (host or "").strip()
    if not ftp_host:
        return False, "FTP 主机不能为空"
    ftp_port = _parse_int(port, 21, 1, 65535)
    ftp_timeout = _parse_int(timeout, 15, 1, 300)
    ftp_username = (username or "").strip() or "anonymous"
    ftp_password = password or ""
    ftp_passive = (passive or "").strip().lower() in {"1", "on", "true", "yes"}
    base_clean = (base_dir or "").strip().strip("/")

    ftp: FTP | None = None
    try:
        ftp = _ftp_connect(ftp_host, ftp_port, ftp_username, ftp_password, ftp_timeout, ftp_passive)
        if base_clean:
            segments = [s for s in base_clean.split("/") if s]
            _ensure_dir(ftp, segments)
        return True, "FTP 连接成功"
    except Exception as exc:
        return False, f"FTP 连接失败: {exc}"
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
