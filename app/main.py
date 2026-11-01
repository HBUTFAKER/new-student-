"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import JSONResponse, Response

from app.api.errors import install_error_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.security import TokenExpiredError, TokenInvalidError, decode_token
from app.db.engine import engine
from app.services.channels import (  # noqa: F401 — 导入即注册 6.2/6.3 通道
    sms,
    wechat_mp,
    wechat_subscribe,
)
from app.services.notification_center import run_notification_worker
from app.services.scheduler import run_scheduler
from app.services.subscription import is_subscription_expired, owner_session

logger = logging.getLogger(__name__)

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _gate_error(request: Request, code: str, message: str) -> dict:
    """构造与 errors.py 同构的错误信封（gate 直接返回响应，不 raise 以避开中间件异常链）。"""

    return {
        "error": {
            "code": code,
            "message": message,
            "trace_id": getattr(request.state, "trace_id", "-"),
        }
    }


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
    async def subscription_gate(request: Request, call_next) -> Response:
        """到期只读闸门（7.2）：默认拦租户写请求，只放 /auth、platform_admin 与无租户身份。

        方向是「默认拦写 + 仅放 /auth」，不是按业务路由白名单逐个放行（会漏）。读接口
        （GET/HEAD/OPTIONS）天然不在此列。每个写请求查一次订阅表可接受（10 人机构 QPS
        极低），不上缓存以免「续费后不生效」。
        """

        if request.method not in _WRITE_METHODS:
            return await call_next(request)
        if request.url.path.startswith("/auth/"):
            return await call_next(request)

        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            # 无 token：公开接口（如 /platform/register）或未登录，交由下游 401/权限处理。
            return await call_next(request)

        try:
            claims = decode_token(authorization[7:].strip(), expected_type="access")
        except (TokenExpiredError, TokenInvalidError):
            # 无效/过期 token 交由 get_principal 返回统一 401，这里不越俎代庖。
            return await call_next(request)

        if claims.role == "platform_admin" or claims.tenant_id is None:
            return await call_next(request)

        try:
            async with owner_session() as db:
                expired = await is_subscription_expired(db, claims.tenant_id)
        except AppError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=_gate_error(request, exc.code, exc.message),
            )

        if expired:
            return JSONResponse(
                status_code=402,
                content=_gate_error(
                    request,
                    "SUBSCRIPTION_EXPIRED",
                    "机构服务已到期，请联系管理员续费（数据已完整保留）",
                ),
            )
        return await call_next(request)

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
