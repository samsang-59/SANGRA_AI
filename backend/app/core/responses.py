"""Turns a service Result into our HTTP response shape: { "data": ... } or { "error": {...} }."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any
from uuid import UUID

from fastapi.responses import JSONResponse

from app.core.errors import error_response
from app.core.result import Result


def encode_value(value: Any) -> Any:
    # Only the types our database rows hold. (FastAPI's generic jsonable_encoder walks every value and
    # was about a third of the time of a project list, B4-L02.)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, PurePath)):
        return str(value)
    raise TypeError(f"Cannot send a {type(value).__name__} as JSON")


class DataResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(content, default=encode_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return DataResponse({"data": data}, status_code=status)


def from_result(result: Result, success_status: int = 200) -> JSONResponse:
    if result.ok:
        return ok(result.data, success_status)
    return error_response(result.error_code or "INTERNAL_ERROR", result.message or "Request failed.")
