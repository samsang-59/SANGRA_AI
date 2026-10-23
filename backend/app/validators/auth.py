"""Validators for the auth routes (Layer 2): only the SHAPE of the request. Unknown extra fields are ignored."""

from typing import Annotated

from pydantic import AfterValidator, EmailStr, StringConstraints

from app.core.security import EMAIL_TOKEN_LENGTH
from app.validators.common import LoginPassword, NewPassword, RequestModel

# Valid email, then stored and compared lowercase.
Email = Annotated[EmailStr, AfterValidator(lambda value: value.lower())]
# 1-100 characters after trimming; emoji and non-English names are fine.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# The token from an email link: exactly the length we create, URL-safe characters only.
EmailToken = Annotated[
    str, StringConstraints(min_length=EMAIL_TOKEN_LENGTH, max_length=EMAIL_TOKEN_LENGTH, pattern=r"^[A-Za-z0-9_-]+$")
]


class RegisterRequest(RequestModel):
    name: Name
    email: Email
    password: NewPassword


class LoginRequest(RequestModel):
    email: Email
    password: LoginPassword


class VerifyEmailRequest(RequestModel):
    token: EmailToken


class ForgotPasswordRequest(RequestModel):
    email: Email


class ResetPasswordRequest(RequestModel):
    token: EmailToken
    new_password: NewPassword


class ChangePasswordRequest(RequestModel):
    old_password: LoginPassword
    new_password: NewPassword
