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
from app.services.channels import (  # noqa: F401 — 导入即注册 6.2/6.3 通道
    sms,
    wechat_mp,
    wechat_subscribe,
)
from app.services.notification_center import run_notification_worker
from app.services.scheduler import run_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the in-process scheduler (or the 6.1 worker), then release on stop.

    The worker needs an owner/superuser connection to scan the pending queue
    across tenants (RLS would otherwise hide every row from ``app_user``). It is
    single-instance safe only — see 08-implementation-delta §0 for the multi-
    instance caveat.

    6.4 起 ``SCHEDULER_ENABLED``（默认开）接管 6.1 的内置 worker：跑三个定时任务（worker
    循环 + 上课提醒扫描 + 余额预警扫描），旧 ``NOTIFICATION_WORKER_ENABLED`` 循环不再启动
    （互斥，防止双循环）。``SCHEDULER_ENABLED=0`` 时回落到 6.1 的纯 worker 循环
    （``NOTIFICATION_WORKER_ENABLED=1``）。
    """

    settings = get_settings()
    task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None
    worker_engine = None

    if settings.scheduler_enabled or settings.notification_worker_enabled:
        worker_engine = create_async_engine(settings.migration_database_url)
        worker_factory = async_sessionmaker(worker_engine, expire_on_commit=False)
        stop_event = asyncio.Event()
        if settings.scheduler_enabled:
            task = asyncio.create_task(
                run_scheduler(
                    worker_factory,
                    worker_interval_seconds=settings.notification_worker_interval_seconds,
                    reminder_interval_seconds=settings.lesson_reminder_scan_interval_seconds,
                    balance_interval_seconds=settings.balance_alert_scan_interval_seconds,
                    stop_event=stop_event,
                ),
                name="notification-scheduler",
            )
        else:
            task = asyncio.create_task(
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
        if task is not None:
            stop_event.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
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
