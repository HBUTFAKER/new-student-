"""Async database engine factory."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings


def build_engine() -> AsyncEngine:
    """Build the process-level async database engine."""

    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,
    )


engine = build_engine()
