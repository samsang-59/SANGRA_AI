"""Auth controller (Layer 3): rate limit check, ONE service call, Result -> HTTP response."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.rate_limit import Rule, rate_limited_response
from app.core.responses import from_result
from app.services import auth_service
from app.services.auth_service import AuthContext
from app.validators.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)


def _ctx(request: Request) -> AuthContext:
    state = request.app.state
    return AuthContext(state.db_pool, state.settings, state.mailer, state.dummy_hash, state.background)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limited(request: Request, checks: list[tuple[Rule, str]]) -> JSONResponse | None:
    result = request.app.state.rate_limiter.check_all(checks)
    return None if result.allowed else rate_limited_response(result)


async def register(request: Request, body: RegisterRequest) -> JSONResponse:
    rules = request.app.state.rate_rules
    if refused := _limited(request, [(rules.register, f"ip:{_client_ip(request)}")]):
        return refused
    return from_result(await auth_service.register(_ctx(request), body.name, body.email, body.password), 201)


async def login(request: Request, body: LoginRequest) -> JSONResponse:
    rules = request.app.state.rate_rules
    if refused := _limited(request, [(rules.login, f"ip:{_client_ip(request)}"), (rules.login, f"email:{body.email}")]):
        return refused
    return from_result(await auth_service.login(_ctx(request), body.email, body.password))


async def verify_email(request: Request, body: VerifyEmailRequest) -> JSONResponse:
    return from_result(await auth_service.verify_email(_ctx(request), body.token))


async def resend_verification(request: Request, user: dict) -> JSONResponse:
    rules = request.app.state.rate_rules
    if refused := _limited(request, [(rules.resend_verification, f"user:{user['id']}")]):
        return refused
    return from_result(await auth_service.resend_verification(_ctx(request), user))


async def forgot_password(request: Request, body: ForgotPasswordRequest) -> JSONResponse:
    rules = request.app.state.rate_rules
    checks = [(rules.forgot_password, f"ip:{_client_ip(request)}"), (rules.forgot_password, f"email:{body.email}")]
    if refused := _limited(request, checks):
        return refused
    return from_result(await auth_service.forgot_password(_ctx(request), body.email))


async def reset_password(request: Request, body: ResetPasswordRequest) -> JSONResponse:
    return from_result(await auth_service.reset_password(_ctx(request), body.token, body.new_password))


async def change_password(request: Request, body: ChangePasswordRequest, user: dict) -> JSONResponse:
    rules = request.app.state.rate_rules
    if refused := _limited(request, [(rules.change_password, f"user:{user['id']}")]):
        return refused
    return from_result(
        await auth_service.change_password(_ctx(request), user, body.old_password, body.new_password))


async def me(user: dict) -> JSONResponse:
    return from_result(auth_service.get_me(user))
