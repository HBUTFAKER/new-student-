"""Application API router."""

from fastapi import APIRouter

from app.api.routes import archive, auth, course, health, ledger, me, platform, trade

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(archive.router)
api_router.include_router(course.router)
api_router.include_router(ledger.router)
api_router.include_router(trade.router)
api_router.include_router(platform.router)
