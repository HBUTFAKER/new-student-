"""HTTP error handlers producing the 0.5 error envelope."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.errors import AppError


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "-")


def _payload(
    request: Request, code: str, message: str, detail: dict | None = None
) -> dict[str, dict]:
    body: dict = {"code": code, "message": message, "trace_id": _trace_id(request)}
    if detail:
        body.update(detail)
    return {"error": body}


def install_error_handlers(app: FastAPI) -> None:
    """Register the single error envelope used by every endpoint."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(request, exc.code, exc.message, exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = f"{location}: {first.get('msg', '请求参数不合法')}".lstrip(": ")
        return JSONResponse(
            status_code=422,
            content=_payload(request, "VALIDATION_ERROR", message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(request, code, detail),
            headers=getattr(exc, "headers", None),
        )
