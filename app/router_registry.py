from fastapi import APIRouter

from app.routers import internal_api, public_api, web


router = APIRouter()
router.include_router(web.router, include_in_schema=False)
router.include_router(internal_api.router, include_in_schema=False)
router.include_router(public_api.router)
