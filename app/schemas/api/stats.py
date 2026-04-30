from pydantic import BaseModel


class SystemStatsResponseSchema(BaseModel):
    total_devices: int
    unreachable_devices: int
    failed_backups_24h: int
