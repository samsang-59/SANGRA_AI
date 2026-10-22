"""The login check, get_current_user (Layer 7, item 4).

Runs before every protected route: reads "Authorization: Bearer <JWT>", verifies the JWT, loads the user,
rejects it if it was issued before users.password_changed_at, and hands the user to the controller.
Any failure is 401 (never 500).

Gaps #1: the JWT issue time has whole-second precision while password_changed_at has microseconds, so
both are compared in whole seconds (password_changed_at is rounded down). A JWT made in the same second
right after a change still works; one from an earlier second does not.
"""

import logging

from fastapi import HTTPException, Request

from app.core.logging import user_id_var
from app.core.security import InvalidTokenError, decode_access_token
from app.repositories import user_repository

logger = logging.getLogger("app.auth")

UNAUTHORIZED_MESSAGE = "Login required, or the session has expired. Please log in again."


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE, headers={"WWW-Authenticate": "Bearer"})


def _bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise _unauthorized()
    scheme, _, token = values[0].strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise _unauthorized()
    return token


async def get_current_user(request: Request) -> dict:
    token = _bearer_token(request)
    settings = request.app.state.settings
    try:
        claims = decode_access_token(token, settings.jwt_secret.get_secret_value())
    except InvalidTokenError:
        raise _unauthorized() from None

    user = await user_repository.find_by_id(request.app.state.db_pool, claims.user_id)
    if user is None:
        raise _unauthorized()
    if claims.issued_at < int(user["password_changed_at"].timestamp()):
        raise _unauthorized()
    user_id_var.set(str(user["id"]))
    return user
