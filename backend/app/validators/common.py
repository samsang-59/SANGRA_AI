"""Field rules shared by the validators (Layer 2)."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints

from app.core.security import BCRYPT_MAX_BYTES

NEW_PASSWORD_MIN_CHARS = 8


def check_new_password(value: str) -> str:
    """Register / reset / change rules: 8+ characters, at least one letter and one digit, at most 72 BYTES
    (bcrypt only reads the first 72 bytes). The byte check comes first, so a huge password is refused
    at once, long before bcrypt could run."""
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"must be at most {BCRYPT_MAX_BYTES} bytes")
    if len(value) < NEW_PASSWORD_MIN_CHARS:
        raise ValueError(f"must be at least {NEW_PASSWORD_MIN_CHARS} characters")
    if not any(ch.isalpha() for ch in value):
        raise ValueError("must contain at least one letter")
    if not any(ch.isdigit() for ch in value):
        raise ValueError("must contain at least one digit")
    return value


NewPassword = Annotated[str, AfterValidator(check_new_password)]
# Login: not empty, no strength rule.
LoginPassword = Annotated[str, StringConstraints(min_length=1)]
