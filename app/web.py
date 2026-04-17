from __future__ import annotations

from fastapi import APIRouter

from app.routers import auth, backups, dashboard, devices, resources, schedules, system, api_v1


router = APIRouter()

router.include_router(dashboard.router, include_in_schema=False)
router.include_router(auth.router, include_in_schema=False)
router.include_router(devices.router, include_in_schema=False)
router.include_router(resources.router, include_in_schema=False)
router.include_router(backups.router, include_in_schema=False)
router.include_router(system.router, include_in_schema=False)
router.include_router(schedules.router, include_in_schema=False)
router.include_router(api_v1.router)
