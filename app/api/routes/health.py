"""Health check endpoints."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DbSession

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", summary="Process health")
async def health() -> dict[str, str]:
    """Return process liveness without touching external dependencies."""

    return {"status": "ok"}


@router.get("/health/db", summary="Database connectivity health")
async def health_db(db: DbSession) -> JSONResponse:
    """Run SELECT 1 and return 503 when PostgreSQL cannot be reached."""

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        # Drivers may raise raw OSError (for example ConnectionRefusedError)
        # before SQLAlchemy wraps them. Every connectivity failure is a 503.
        logger.exception("database_health_check_failed")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unavailable"},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "database": "ok"})
