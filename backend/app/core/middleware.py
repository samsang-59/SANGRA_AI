"""Pure ASGI middleware: request id + unexpected-error catcher, and the request body size limit."""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import INTERNAL_ERROR_MESSAGE, error_body
from app.core.logging import request_id_var, run_id_var, user_id_var

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware:
    """Gives every request an id (in the logs and in the X-Request-ID response header).

    Any unexpected exception is logged in full (with the request id) and answered with a plain
    500 INTERNAL_ERROR in our shape: no stack trace, no file names.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        tokens = (request_id_var.set(request_id), user_id_var.set("-"), run_id_var.set("-"))
        started = time.perf_counter()
        response_started = False
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("Unexpected error while handling %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                # The response is half sent; the only safe thing left is to drop the connection.
                raise
            state = scope.get("state") or {}
            if state.get("body_too_large"):
                status_code = 413
                body = error_body("PAYLOAD_TOO_LARGE", f"The request body is larger than the limit of {state['body_limit']} bytes.")
            else:
                status_code = 500
                body = error_body("INTERNAL_ERROR", INTERNAL_ERROR_MESSAGE)
            await _send_json(send_with_id, status_code, body)
        finally:
            logger.info(
                "%s %s -> %s (%.1f ms)",
                scope.get("method"),
                scope.get("path"),
                status_code,
                (time.perf_counter() - started) * 1000,
            )
            for var, token in zip((request_id_var, user_id_var, run_id_var), tokens):
                var.reset(token)


class BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    """Refuses request bodies over the limit with 413 (Gaps #2).

    The limit is chosen per request (normal routes 1 MB, the zip upload route 50 MB). A body with a
    Content-Length over the limit is refused before a single byte is read. A body without a length
    is counted while it streams in and refused as soon as it passes the limit (never fully buffered).
    """

    def __init__(self, app: ASGIApp, limit_for: Callable[[Scope], int]):
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope)
        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > limit
            except ValueError:
                too_large = False
            if too_large:
                await _send_json(send, 413, error_body(
                    "PAYLOAD_TOO_LARGE", f"The request body is larger than the limit of {limit} bytes."))
                return

        received = 0
        state = scope.setdefault("state", {})

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    state["body_too_large"] = True
                    state["body_limit"] = limit
                    raise BodyTooLargeError()
            return message

        await self.app(scope, counting_receive, send)


def upload_aware_limit(normal_limit: int, upload_limit: int) -> Callable[[Scope], int]:
    """The zip upload route (POST /api/v1/projects/{id}/runs) gets the upload limit; every other route the normal one."""
    upload_path = re.compile(r"^/api/v1/projects/[^/]+/runs/?$")

    def limit_for(scope: Scope) -> int:
        if scope.get("method") == "POST" and upload_path.match(scope.get("path", "")):
            return upload_limit
        return normal_limit

    return limit_for


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _send_json(send: Send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
    })
    await send({"type": "http.response.body", "body": payload})
