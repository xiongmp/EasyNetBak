from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.services.crypto import decrypt_secret, encrypt_secret


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    role: str = "admin"
    group_access_type: str = Field(default="all")  # all, specific
    allowed_group_ids: Optional[str] = None  # Comma separated IDs, e.g. "1,2,5". Special value "-1" for Ungrouped.
    password_hash: str
    password_expired: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class LoginLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status: str = Field(default="fail")  # success, fail, logout
    fail_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class Credential(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    username: str
    encrypted_password: Optional[str] = Field(default=None, alias="password")
    encrypted_enable_password: Optional[str] = Field(default=None, alias="enable_password")
    remarks: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    def __init__(self, **kwargs):
        # Handle 'password' and 'enable_password' from kwargs to support standard initialization
        pwd = kwargs.pop("password", None)
        enable_pwd = kwargs.pop("enable_password", None)
        super().__init__(**kwargs)
        if pwd is not None:
            self.password = pwd
        if enable_pwd is not None:
            self.enable_password = enable_pwd

    @property
    def password(self) -> str | None:
        return decrypt_secret(self.encrypted_password)

    @password.setter
    def password(self, value: str | None):
        self.encrypted_password = encrypt_secret(value)

    @property
    def enable_password(self) -> str | None:
        return decrypt_secret(self.encrypted_enable_password)

    @enable_password.setter
    def enable_password(self, value: str | None):
        self.encrypted_enable_password = encrypt_secret(value)



class DeviceGroup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class BackupTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    platform: str
    commands: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class BackupSchedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    crontab: str
    enabled: bool = False
    targets: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class BackupScheduleRun(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    schedule_id: int = Field(index=True)
    trigger: str = "manual"
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)
    total_devices: int = 0
    success_count: int = 0
    fail_count: int = 0
    error_message: Optional[str] = None


class BackupScheduleRunItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: UUID = Field(index=True)
    schedule_id: int = Field(index=True)
    backup_id: UUID = Field(index=True)
    device_id: int = Field(index=True)


class Device(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    host: str = Field(index=True)
    port: int = 22
    login_method: str = "ssh"
    platform: str
    group_id: Optional[int] = Field(default=None, index=True)
    credential_id: Optional[int] = Field(default=None, index=True)
    default_template_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # Reachability Status
    reachability_status: Optional[bool] = Field(default=None)
    last_reachability_check: Optional[datetime] = Field(default=None)
    reachability_error: Optional[str] = Field(default=None)
    reachability_duration_ms: Optional[int] = Field(default=None)


class BackupRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    device_id: int = Field(index=True)
    template_id: Optional[int] = Field(default=None, index=True)
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)
    success: bool = False
    config_text: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    failure_type: Optional[str] = None
    config_snapshot_hash: Optional[str] = None


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    username: Optional[str] = Field(default=None, index=True)
    action: str = Field(index=True)  # e.g., "DELETE_DEVICE", "UPDATE_CREDENTIAL"
    resource_type: str = Field(index=True)  # e.g., "device", "credential"
    resource_id: Optional[str] = Field(default=None, index=True)
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
