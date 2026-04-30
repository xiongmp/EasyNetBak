from fastapi import APIRouter

from app.routers.internal_api import backups, devices, schedules


router = APIRouter()
router.include_router(devices.router)
router.include_router(backups.router)
router.include_router(schedules.router)
