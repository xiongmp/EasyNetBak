from __future__ import annotations

import time
from uuid import UUID

from app import crud
from app.db import session_scope
from app.services.backup_service import backup_device
from app.platforms import platforms_compatible
from app.services.alert_service import check_and_alert
from app.services.s3_service import upload_backup_to_s3
from app.services.ftp_service import upload_backup_to_ftp
from app.services.netmiko_client import NetmikoClientError


def run_backup_record(record_id: UUID, device_id: int, template_id: int | None, skip_email: bool = False) -> None:
    with session_scope() as session:
        device = crud.get_device(session, device_id)
        if device is None:
            record = crud.finish_backup_record(
                session, record_id=record_id, success=False, config_text=None, error_message="Device not found",
                failure_type="DEVICE_NOT_FOUND"
            )
            if record:
                check_and_alert(session, record, skip_email=skip_email)
            return
        tpl_id = template_id or getattr(device, "default_template_id", None)
        template = crud.get_template(session, tpl_id) if tpl_id else None
        if tpl_id and template is None:
            record = crud.finish_backup_record(
                session, record_id=record_id, success=False, config_text=None, error_message="Template not found",
                failure_type="TEMPLATE_NOT_FOUND"
            )
            if record:
                check_and_alert(session, record, skip_email=skip_email)
            return
        if template is not None and not platforms_compatible(template.platform, device.platform):
            record = crud.finish_backup_record(
                session,
                record_id=record_id,
                success=False,
                config_text=None,
                error_message="Template platform mismatch",
                failure_type="PLATFORM_MISMATCH"
            )
            if record:
                check_and_alert(session, record, skip_email=skip_email)
            return
        secrets = crud.get_device_secrets(session, device)

        start_time = time.time()
        try:
            if not secrets.get("username"):
                raise RuntimeError("Missing credential for device")
            config_text = backup_device(
                host=device.host,
                port=device.port,
                login_method=getattr(device, "login_method", "ssh") or "ssh",
                encoding=getattr(device, "encoding", "utf-8") or "utf-8",
                platform=device.platform,
                username=secrets["username"],
                password=secrets["password"],
                enable_password=secrets["enable_password"],
                template_commands=template.commands if template else None,
            )
            duration = time.time() - start_time
            record = crud.finish_backup_record(
                session, record_id=record_id, success=True, config_text=config_text, error_message=None,
                duration_seconds=duration, failure_type=None
            )
            if record:
                # 尝试上传到 S3 (如果启用)
                upload_backup_to_s3(
                    session=session,
                    device_name=device.name,
                    host=device.host,
                    config_text=config_text,
                    finished_at=record.finished_at or record.started_at
                )
                upload_backup_to_ftp(
                    session=session,
                    device_name=device.name,
                    host=device.host,
                    config_text=config_text,
                    finished_at=record.finished_at or record.started_at
                )
                check_and_alert(session, record, skip_email=skip_email)
        except Exception as exc:
            duration = time.time() - start_time
            failure_type = "UNKNOWN"
            if isinstance(exc, NetmikoClientError):
                failure_type = exc.failure_type
            
            record = crud.finish_backup_record(
                session,
                record_id=record_id,
                success=False,
                config_text=None,
                error_message=str(exc),
                duration_seconds=duration,
                failure_type=failure_type
            )
            if record:
                check_and_alert(session, record, skip_email=skip_email)
