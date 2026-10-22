"""Auth routes (Layer 1). Public routes have no login check; protected ones depend on get_current_user."""

from fastapi import APIRouter, Depends, Request

from app.controllers import auth_controller
from app.core.auth import get_current_user
from app.validators.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# Public
@router.post("/register", status_code=201)
async def register(request: Request, body: RegisterRequest):
    return await auth_controller.register(request, body)


@router.post("/login")
async def login(request: Request, body: LoginRequest):
    return await auth_controller.login(request, body)


@router.post("/verify-email")
async def verify_email(request: Request, body: VerifyEmailRequest):
    return await auth_controller.verify_email(request, body)


@router.post("/forgot-password")
async def forgot_password(request: Request, body: ForgotPasswordRequest):
    return await auth_controller.forgot_password(request, body)


@router.post("/reset-password")
async def reset_password(request: Request, body: ResetPasswordRequest):
    return await auth_controller.reset_password(request, body)


# Protected
@router.post("/resend-verification")
async def resend_verification(request: Request, user: dict = Depends(get_current_user)):
    return await auth_controller.resend_verification(request, user)


@router.post("/change-password")
async def change_password(request: Request, body: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    return await auth_controller.change_password(request, body, user)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return await auth_controller.me(user)
