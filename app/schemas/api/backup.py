from __future__ import annotations

from pydantic import BaseModel

from app.schemas.api.common import PageResponse


class BackupContentResponseSchema(BaseModel):
    config_text: str | None = None


class DeviceBackupItemSchema(BaseModel):
    id: str
    started_at: str
    finished_at: str | None = None
    success: bool
    error_message: str | None = None
    config_snapshot_hash: str | None = None


class DeviceBackupsResponseSchema(PageResponse[DeviceBackupItemSchema]):
    pass
