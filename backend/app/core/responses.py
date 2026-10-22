"""Turns a service Result into our HTTP response shape: { "data": ... } or { "error": {...} }."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.errors import error_response
from app.core.result import Result


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": jsonable_encoder(data)}, status_code=status)


def from_result(result: Result, success_status: int = 200) -> JSONResponse:
    if result.ok:
        return ok(result.data, success_status)
    return error_response(result.error_code or "INTERNAL_ERROR", result.message or "Request failed.")
