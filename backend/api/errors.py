import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """A safe, expected error that may be returned to API clients."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "BUSINESS_ERROR",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


def _error_content(
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"code": code}
    if details:
        data["details"] = details
    return {
        "success": False,
        "data": data,
        "message": message,
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def handle_business_error(
        request: Request,
        exc: BusinessError,
    ) -> JSONResponse:
        logger.warning(
            "Business API error: %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "location": [str(part) for part in error.get("loc", ())],
                "message": error.get("msg", "参数无效"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        logger.warning(
            "API validation error: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=422,
            content=_error_content("VALIDATION_ERROR", "请求参数错误", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content("HTTP_ERROR", message),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled API exception: %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content=_error_content("INTERNAL_ERROR", "服务器内部错误"),
        )

