from fastapi import APIRouter

from app.routers.web import auth, backups, dashboard, devices, resources, schedules, system


router = APIRouter()
router.include_router(dashboard.router)
router.include_router(auth.router)
router.include_router(devices.router)
router.include_router(resources.router)
router.include_router(backups.router)
router.include_router(system.router)
router.include_router(schedules.router)
