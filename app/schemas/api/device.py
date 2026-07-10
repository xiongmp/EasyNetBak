from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from app.schemas.api.common import PageResponse
from app.schemas.inputs import DeviceCreateInput, DeviceUpdateInput


class DeviceResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    login_method: str
    encoding: str
    platform: str
    group_id: int | None = None
    credential_id: int | None = None
    default_template_id: int | None = None
    created_at: datetime
    reachability_status: bool | None = None
    last_reachability_check: datetime | None = None
    reachability_error: str | None = None
    reachability_duration_ms: int | None = None


class DeviceListResponseSchema(PageResponse[DeviceResponseSchema]):
    pass


class DeviceCreateSchema(DeviceCreateInput):
    pass


class DeviceUpdateSchema(DeviceUpdateInput):
    pass
