from fastapi import APIRouter

from app.routers.public_api import v1


router = APIRouter()
router.include_router(v1.router)
