"""Application API router."""

from fastapi import APIRouter

from app.api.routes import (
    archive,
    attendance,
    auth,
    course,
    health,
    holiday,
    ledger,
    lesson,
    me,
    org_config,
    platform,
    report,
    schedule,
    trade,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(archive.router)
api_router.include_router(course.router)
api_router.include_router(schedule.router)
api_router.include_router(holiday.router)
api_router.include_router(lesson.router)
api_router.include_router(attendance.router)
api_router.include_router(org_config.router)
api_router.include_router(report.router)
api_router.include_router(ledger.router)
api_router.include_router(trade.router)
api_router.include_router(platform.router)
