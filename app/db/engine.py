"""Async database engine factory."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def build_engine() -> AsyncEngine:
    """Build the process-level async database engine."""

    settings = get_settings()
    options: dict[str, object] = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
    }
    if settings.app_env == "test":
        # pytest creates one event loop per test; pooled asyncpg connections
        # would otherwise be reused on a closed loop.
        options["poolclass"] = NullPool
    return create_async_engine(settings.database_url, **options)


engine = build_engine()
