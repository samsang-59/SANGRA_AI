"""Run controller (Layer 3): ONE service call, Result -> HTTP response. Also the live stream (SSE) format."""

import json
import time
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.core.responses import encode_value, from_result
from app.services import run_service
from app.services.run_service import RunContext
from app.validators.run import ApprovalRequest, HintRequest, RunListQuery, StartRunRequest

ZIP_FIELD = "zip_file"
MAX_SEQUENCE = 2**31 - 1


def _ctx(request: Request) -> RunContext:
    state = request.app.state
    return RunContext(state.db_pool, state.settings, state.agent_runner, state.run_progress, state.run_cleanup)


def _invalid(loc: str, message: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", loc), "msg": message, "type": "value_error"}])


async def start(request: Request, project_id: int, user: dict) -> JSONResponse:
    """POST /projects/{id}/runs: JSON {bug_description}, or multipart with bug_description and zip_file
    (zip projects). The whole upload is already capped by the body size limit while it streams in."""
    settings = request.app.state.settings
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=10)
        try:
            upload = form.get(ZIP_FIELD)
            if upload is not None and not isinstance(upload, UploadFile):
                raise _invalid(ZIP_FIELD, "must be a file")
            if upload is not None:
                if not (upload.filename or "").lower().endswith(".zip"):
                    raise _invalid(ZIP_FIELD, "must be a .zip file")
                if upload.size is not None and upload.size > settings.max_upload_bytes:
                    raise _invalid(ZIP_FIELD, f"must be at most {settings.max_upload_bytes // (1024 * 1024)} MB")
            body = _validate(StartRunRequest, {"bug_description": form.get("bug_description")})
            return from_result(await run_service.start_run(
                _ctx(request), user, project_id, body.bug_description, upload.file if upload else None), 201)
        finally:
            await form.close()
    try:
        payload = await request.json()
    except ValueError:
        raise RequestValidationError([{"loc": ("body",), "msg": "", "type": "json_invalid"}]) from None
    body = _validate(StartRunRequest, payload)
    return from_result(await run_service.start_run(_ctx(request), user, project_id, body.bug_description, None), 201)


def _validate(model, payload):
    if not isinstance(payload, dict):
        raise _invalid("body", "must be an object")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_input=False)]) from None


async def list_mine(request: Request, query: RunListQuery, user: dict) -> JSONResponse:
    return from_result(await run_service.list_runs(_ctx(request), user, query.project_id, query.status, query.page,
                                                   query.limit))


async def get_one(request: Request, run_id: int, user: dict) -> JSONResponse:
    return from_result(await run_service.get_run(_ctx(request), user, run_id))


def last_event_id(header: str | None) -> int:
    """The Last-Event-ID header: a number the browser already has. Anything odd counts as 0 (send all)."""
    try:
        value = int((header or "").strip())
    except ValueError:
        return 0
    return value if 0 <= value <= MAX_SEQUENCE else 0


def format_event(event: dict) -> bytes:
    data = json.dumps({
        "sequence_number": event["sequence_number"],
        "event_type": event["event_type"],
        "message": event["message"],
        "details": event["details"],
        "attempt_id": event["attempt_id"],
        "created_at": event["created_at"],
    }, default=encode_value, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence_number']}\nevent: {event['event_type']}\ndata: {data}\n\n".encode()


PING = b": ping\n\n"


async def stream(request: Request, run_id: int, user: dict):
    ctx = _ctx(request)
    result = await run_service.open_event_stream(ctx, user, run_id, last_event_id(request.headers.get("last-event-id")))
    if not result.ok:
        return from_result(result)
    batches = result.data
    ping_every = ctx.settings.stream_ping_seconds

    async def body() -> AsyncIterator[bytes]:
        last_sent = time.monotonic()
        try:
            async for batch in batches:
                for event in batch:
                    yield format_event(event)
                if batch:
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= ping_every:
                    yield PING
                    last_sent = time.monotonic()
        finally:
            await batches.aclose()

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def approval(request: Request, run_id: int, body: ApprovalRequest, user: dict) -> JSONResponse:
    return from_result(await run_service.submit_approval(_ctx(request), user, run_id, body.attempt_id, body.decision,
                                                         body.feedback))


async def hint(request: Request, run_id: int, body: HintRequest, user: dict) -> JSONResponse:
    return from_result(await run_service.submit_hint(_ctx(request), user, run_id, body.text))


async def cancel(request: Request, run_id: int, user: dict) -> JSONResponse:
    return from_result(await run_service.cancel_run(_ctx(request), user, run_id))


async def delete(request: Request, run_id: int, user: dict) -> JSONResponse:
    return from_result(await run_service.delete_run(_ctx(request), user, run_id))
