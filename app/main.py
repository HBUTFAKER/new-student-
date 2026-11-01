"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import Response

from app.api.errors import install_error_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.engine import engine
from app.services.notification_center import run_notification_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the in-process notification worker, then release resources on stop.

    The worker needs an owner/superuser connection to scan the pending queue
    across tenants (RLS would otherwise hide every row from ``app_user``). It is
    single-instance safe only — see 08-implementation-delta §0 for the multi-
    instance caveat. ``NOTIFICATION_WORKER_ENABLED=0`` turns it off (CI/tests).
    """

    settings = get_settings()
    worker: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None
    worker_engine = None

    if settings.notification_worker_enabled:
        worker_engine = create_async_engine(settings.migration_database_url)
        worker_factory = async_sessionmaker(worker_engine, expire_on_commit=False)
        stop_event = asyncio.Event()
        worker = asyncio.create_task(
            run_notification_worker(
                worker_factory,
                interval_seconds=settings.notification_worker_interval_seconds,
                stop_event=stop_event,
            ),
            name="notification-worker",
        )

    try:
        yield
    finally:
        if worker is not None:
            stop_event.set()
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        if worker_engine is not None:
            await worker_engine.dispose()
        await engine.dispose()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""

    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_logging(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid4().hex)
        request.state.trace_id = request_id
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "extra_fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                    }
                },
            )
            raise

        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
            },
        )
        return response

    application.include_router(api_router)
    install_error_handlers(application)
    return application


app = create_app()
