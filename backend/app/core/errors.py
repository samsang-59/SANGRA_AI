"""Error handling (Layer 3 response shape + Layer 7, item 6).

Every error leaves the backend in one shape: { "error": { "code": "...", "message": "..." } }.
Unexpected errors are logged with the request id and answered with a plain 500 with no internal detail.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

# Error code -> HTTP status (04_backend_design.md, Layer 3 and "Error codes added by services").
ERROR_STATUS: dict[str, int] = {
    "EMAIL_ALREADY_REGISTERED": 409,
    "INVALID_CREDENTIALS": 401,
    "EMAIL_NOT_VERIFIED": 403,
    "TOKEN_INVALID_OR_EXPIRED": 400,
    "NOT_FOUND": 404,
    "ACTIVE_RUN_EXISTS": 409,
    "RUN_STATE_INVALID": 409,
    "STALE_ATTEMPT": 409,
    "LOCAL_PATH_NOT_ALLOWED": 400,
    "ZIP_REQUIRED": 400,
    "PACKAGE_EXPIRED": 410,
    "TOO_SOON": 429,
    "PATH_NOT_FOUND": 400,
    "PROJECT_HAS_ACTIVE_RUN": 409,
    "INVALID_PROJECT_CONFIG": 400,
    "PROJECT_LIMIT_REACHED": 409,
    "WRONG_OLD_PASSWORD": 400,
    "NEW_PASSWORD_SAME_AS_OLD": 400,
    "SOURCE_CHANGED": 409,
    # From other layers.
    "UNAUTHORIZED": 401,
    "RATE_LIMITED": 429,
    "VALIDATION_ERROR": 422,
    "INTERNAL_ERROR": 500,
    # Framework-level errors that also need our shape (B0-X03, X04, X07, X09).
    "METHOD_NOT_ALLOWED": 405,
    "PAYLOAD_TOO_LARGE": 413,
    "SERVICE_UNAVAILABLE": 503,
}

# HTTP status -> code, for errors raised by the framework itself (unknown URL, wrong method ...).
_STATUS_CODE_NAMES: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "The request could not be read."),
    401: ("UNAUTHORIZED", "Login required."),
    404: ("NOT_FOUND", "Not found."),
    405: ("METHOD_NOT_ALLOWED", "This method is not allowed on this URL."),
    413: ("PAYLOAD_TOO_LARGE", "The request body is too large."),
}

INTERNAL_ERROR_MESSAGE = "Something went wrong on the server."


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def error_response(code: str, message: str, status: int | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status or ERROR_STATUS.get(code, 500), headers=headers)


def body_too_large_response(limit: int) -> JSONResponse:
    return error_response("PAYLOAD_TOO_LARGE", f"The request body is larger than the limit of {limit} bytes.")


def _field_name(loc: tuple) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in ("body", "query", "path", "header"):
        parts = parts[1:] or parts
    return ".".join(parts) or "body"


def validation_message(errors: list[dict]) -> str:
    """Names every bad field and the problem. Never repeats the bad value (it may be a password)."""
    problems = []
    for error in errors:
        if error.get("type") == "json_invalid":
            problems.append("body: the JSON could not be read")
            continue
        message = str(error.get("msg", "is invalid")).removeprefix("Value error, ")
        problems.append(f"{_field_name(tuple(error.get('loc', ())))}: {message}")
    return "Invalid request. " + "; ".join(problems)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if getattr(request.state, "body_too_large", False):
        return body_too_large_response(request.state.body_limit)
    code, default_message = _STATUS_CODE_NAMES.get(exc.status_code, ("HTTP_ERROR", "Request failed."))
    message = exc.detail if isinstance(exc.detail, str) and exc.status_code not in (404, 405) else default_message
    if exc.status_code >= 500:
        message = INTERNAL_ERROR_MESSAGE
    return error_response(code, message, status=exc.status_code, headers=getattr(exc, "headers", None))


async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response("VALIDATION_ERROR", validation_message(list(exc.errors())))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
