"""Domain errors and the consistent error envelope returned by the API.

Every error response has the same shape:

    {
      "error": {"code": "VALIDATION_ERROR", "message": "...", "details": [...]},
      "request_id": "3f2c..."
    }
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("api.errors")


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: Any = None, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.extra = extra


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class LogsNotFoundError(AppError):
    status_code = 404
    code = "LOGS_NOT_FOUND"


class BatchValidationError(AppError):
    """No log in a batch was valid. Same code and shape as a request validation error."""

    status_code = 422
    code = "VALIDATION_ERROR"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class InvalidImportError(AppError):
    """The uploaded file cannot be read (encoding, format or too many records)."""

    status_code = 422
    code = "INVALID_IMPORT"


class NoLogsMatchedError(AppError):
    status_code = 422
    code = "NO_LOGS_MATCHED"


class TooManyLogsError(AppError):
    status_code = 422
    code = "TOO_MANY_LOGS"


class LLMError(AppError):
    """Base class for failures talking to the LLM provider."""

    status_code = 502
    code = "LLM_ERROR"


class LLMTimeoutError(LLMError):
    status_code = 504
    code = "LLM_TIMEOUT"


class LLMResponseFormatError(LLMError):
    status_code = 502
    code = "LLM_INVALID_RESPONSE"

    def __init__(self, message: str, raw_response: str | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.raw_response = raw_response


class LLMProviderError(LLMError):
    status_code = 502
    code = "LLM_PROVIDER_ERROR"


class DatabaseUnavailableError(AppError):
    status_code = 503
    code = "DATABASE_UNAVAILABLE"


HTTP_CODE_NAMES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
}


def error_body(request: Request, code: str, message: str, details: Any = None, **extra: Any):
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    body["error"].update(extra)
    body["request_id"] = getattr(request.state, "request_id", None)
    return jsonable_encoder(body)


def clean_validation_errors(errors: list[dict]) -> list[dict]:
    """Keep only what a client needs: where, what and why."""
    cleaned = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ())]
        cleaned.append(
            {
                "field": (".".join(loc[1:]) if loc and loc[0] in {"body", "query", "path"} else ".".join(loc))
                or None,
                "location": loc[0] if loc else None,
                "message": err.get("msg"),
                "type": err.get("type"),
            }
        )
    return cleaned


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        logger.log(level, "request failed", extra={"error_code": exc.code, "detail": exc.message})
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(request, exc.code, exc.message, exc.details, **exc.extra),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_body(
                request,
                "VALIDATION_ERROR",
                "The request is not valid.",
                clean_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = HTTP_CODE_NAMES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").capitalize()
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(request, code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content=error_body(request, "INTERNAL_ERROR", "An unexpected error occurred."),
        )
