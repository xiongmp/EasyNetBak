from fastapi import APIRouter
from app.routers.public_api.v1 import backups
from app.routers.public_api.v1 import credentials
from app.routers.public_api.v1 import devices
from app.routers.public_api.v1 import groups
from app.routers.public_api.v1 import stats
from app.routers.public_api.v1 import templates


router = APIRouter()
router.include_router(devices.router)
router.include_router(groups.router)
router.include_router(credentials.router)
router.include_router(templates.router)
router.include_router(backups.router)
router.include_router(stats.router)

