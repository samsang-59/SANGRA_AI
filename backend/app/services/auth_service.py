"""AuthService (Layer 4): register, login, verify email, resend, forgot, reset, change password, me.

Returns a Result, never an HTTP error. The repository saves; this service makes the decisions, creates
tokens and JWTs, and sends emails through the mailer.
"""

import logging
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from app.core.background import BackgroundTasks
from app.core.config import Settings
from app.core.mailer import Mailer, deliver
from app.core.result import Result
from app.core.security import (
    DummyHash,
    check_login_password,
    create_access_token,
    hash_email_token,
    hash_password,
    new_email_token,
    verify_password,
)
from app.repositories import user_repository

logger = logging.getLogger("app.auth")

FORGOT_PASSWORD_MESSAGE = "If this email exists, we sent a link to reset the password."
INVALID_CREDENTIALS_MESSAGE = "Wrong email or password."
TOKEN_INVALID_MESSAGE = "This link is invalid or has expired."


@dataclass(frozen=True)
class AuthContext:
    pool: AsyncConnectionPool
    settings: Settings
    mailer: Mailer
    dummy_hash: DummyHash
    background: BackgroundTasks


def public_user(user: dict) -> dict:
    """The user as the browser may see it (never the password hash)."""
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "is_email_verified": user["is_email_verified"],
        "created_at": user["created_at"],
    }


def _jwt(ctx: AuthContext, user_id: int) -> str:
    return create_access_token(user_id, ctx.settings.jwt_secret.get_secret_value(), ctx.settings.jwt_lifetime_hours)


def _link(ctx: AuthContext, page: str, token: str) -> str:
    return f"{ctx.settings.frontend_url.rstrip('/')}/{page}?token={token}"


async def _send_verification(ctx: AuthContext, email: str, token: str) -> bool:
    body = (
        "Welcome to SANGRA_AI.\n\n"
        f"Please confirm your email address by opening this link:\n{_link(ctx, 'verify-email', token)}\n\n"
        f"The link works once and expires in {ctx.settings.verification_token_hours} hours."
    )
    return await deliver(ctx.mailer, email, "Verify your email for SANGRA_AI", body)


async def _send_reset(ctx: AuthContext, email: str, token: str) -> bool:
    body = (
        "Someone asked to reset the password of your SANGRA_AI account.\n\n"
        f"To choose a new password, open this link:\n{_link(ctx, 'reset-password', token)}\n\n"
        f"The link works once and expires in {ctx.settings.reset_token_minutes} minutes. "
        "If you did not ask for this, you can ignore this email."
    )
    return await deliver(ctx.mailer, email, "Reset your SANGRA_AI password", body)


async def register(ctx: AuthContext, name: str, email: str, password: str) -> Result:
    if await user_repository.find_by_email(ctx.pool, email) is not None:
        return Result.failure("EMAIL_ALREADY_REGISTERED", "This email is already registered.")
    password_hash = await hash_password(password, ctx.settings.bcrypt_rounds)
    token = new_email_token()
    user = await user_repository.create_user_with_verification_token(
        ctx.pool, name, email, password_hash, hash_email_token(token), ctx.settings.verification_token_hours * 3600
    )
    if user is None:  # the unique rule in the database is the final guard (two registers at once)
        return Result.failure("EMAIL_ALREADY_REGISTERED", "This email is already registered.")
    if not await _send_verification(ctx, email, token):
        logger.warning("Verification email for new user %s was not sent; the user can resend it", user["id"])
    return Result.success({"user": public_user(user), "token": _jwt(ctx, user["id"])})


async def login(ctx: AuthContext, email: str, password: str) -> Result:
    user = await user_repository.find_by_email(ctx.pool, email)
    password_hash = user["password_hash"] if user else None
    if not await check_login_password(password, password_hash, ctx.dummy_hash) or user is None:
        return Result.failure("INVALID_CREDENTIALS", INVALID_CREDENTIALS_MESSAGE)
    return Result.success({"user": public_user(user), "token": _jwt(ctx, user["id"])})


async def verify_email(ctx: AuthContext, token: str) -> Result:
    user = await user_repository.verify_email_with_token(ctx.pool, hash_email_token(token))
    if user is None:
        return Result.failure("TOKEN_INVALID_OR_EXPIRED", TOKEN_INVALID_MESSAGE)
    return Result.success({"message": "Your email is verified."})


async def resend_verification(ctx: AuthContext, user: dict) -> Result:
    if user["is_email_verified"]:
        return Result.success({"message": "Your email is already verified."})
    latest = await user_repository.get_latest_token_time(ctx.pool, user["id"], "email_verification")
    if latest is not None and latest["age_seconds"] < ctx.settings.resend_verification_min_seconds:
        wait = max(1, int(ctx.settings.resend_verification_min_seconds - latest["age_seconds"]) + 1)
        return Result.failure("TOO_SOON", f"A link was sent a moment ago. Try again in {wait} seconds.")
    token = new_email_token()
    await user_repository.create_token(
        ctx.pool, user["id"], "email_verification", hash_email_token(token), ctx.settings.verification_token_hours * 3600
    )
    await _send_verification(ctx, user["email"], token)
    return Result.success({"message": "We sent a new verification link."})


async def forgot_password(ctx: AuthContext, email: str) -> Result:
    user = await user_repository.find_by_email(ctx.pool, email)
    if user is not None:
        token = new_email_token()
        await user_repository.create_token(
            ctx.pool, user["id"], "password_reset", hash_email_token(token), ctx.settings.reset_token_minutes * 60
        )
        # Sent after the answer, so the answer time does not tell whether the email exists.
        ctx.background.spawn(_send_reset(ctx, user["email"], token), name="send-reset-email")
    return Result.success({"message": FORGOT_PASSWORD_MESSAGE})


async def reset_password(ctx: AuthContext, token: str, new_password: str) -> Result:
    new_hash = await hash_password(new_password, ctx.settings.bcrypt_rounds)
    user_id = await user_repository.reset_password_with_token(ctx.pool, hash_email_token(token), new_hash)
    if user_id is None:
        return Result.failure("TOKEN_INVALID_OR_EXPIRED", TOKEN_INVALID_MESSAGE)
    return Result.success({"message": "Your password was changed. Please log in with the new password."})


async def change_password(ctx: AuthContext, user: dict, old_password: str, new_password: str) -> Result:
    if not await verify_password(old_password, user["password_hash"]):
        return Result.failure("WRONG_OLD_PASSWORD", "The old password is wrong.")
    if new_password == old_password:
        return Result.failure("NEW_PASSWORD_SAME_AS_OLD", "The new password must be different from the old one.")
    new_hash = await hash_password(new_password, ctx.settings.bcrypt_rounds)
    await user_repository.update_password(ctx.pool, user["id"], new_hash)
    return Result.success({"message": "Password changed. Please log in again."})


def get_me(user: dict) -> Result:
    return Result.success(public_user(user))
