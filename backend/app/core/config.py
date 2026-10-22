"""Settings (Layer 7, item 1).

One place that reads the .env file. Checked at startup: the app refuses to start with a clear
message if something is missing or wrong. Secrets are kept as SecretStr so they never show up in
a repr or a log line, and validation messages never repeat the bad value.
"""

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The placeholder written in .env.example. Starting with it is refused (B2-S03).
PLACEHOLDER_JWT_SECRET = "replace-with-a-random-string-of-at-least-32-characters"
MIN_JWT_SECRET_LENGTH = 32


class SettingsError(Exception):
    """The settings are missing or wrong. The message names every bad setting, never its value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Required (no default): the app refuses to start without them.
    app_mode: Literal["local", "deployed"]
    database_url: SecretStr
    groq_api_key: SecretStr
    groq_model: str = Field(min_length=1)
    jwt_secret: SecretStr
    frontend_url: str = Field(min_length=1)
    cors_origins: Annotated[list[str], NoDecode]
    workspaces_dir: Path
    packages_dir: Path

    # Optional, with the defaults agreed in the design.
    jwt_lifetime_hours: int = Field(default=24, gt=0)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db_pool_min_size: int = Field(default=1, gt=0)
    db_pool_max_size: int = Field(default=10, gt=0)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0)
    docker_check_timeout_seconds: float = Field(default=2.0, gt=0)
    # Body size limits (Gaps #2): 1 MB on normal routes, 50 MB on the zip upload route.
    max_body_bytes: int = Field(default=1_048_576, gt=0)
    max_upload_bytes: int = Field(default=52_428_800, gt=0)

    # Email links (03_database_design.md section 7 / 04_backend_design.md AuthService).
    verification_token_hours: int = Field(default=24, gt=0)
    reset_token_minutes: int = Field(default=30, gt=0)
    resend_verification_min_seconds: int = Field(default=60, ge=0)

    # Projects: at most this many per user.
    project_limit: int = Field(default=20, gt=0)

    # Passwords: bcrypt cost (each +1 doubles the time; 12 is about 0.25 s per hash).
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Mailer (Layer 7, item 8): "console" prints the email in the terminal (development), "smtp" sends it.
    mailer_mode: Literal["console", "smtp"] = "console"
    mail_from: str = "SANGRA_AI <no-reply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, lt=65536)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=10.0, gt=0)

    # Rate limits (Layer 7, item 5): attempts per window.
    rate_limit_login: int = Field(default=10, gt=0)
    rate_limit_login_window_seconds: int = Field(default=15 * 60, gt=0)
    rate_limit_register: int = Field(default=5, gt=0)
    rate_limit_register_window_seconds: int = Field(default=60 * 60, gt=0)
    rate_limit_forgot_password: int = Field(default=3, gt=0)
    rate_limit_forgot_password_window_seconds: int = Field(default=60 * 60, gt=0)
    rate_limit_resend_verification: int = Field(default=1, gt=0)
    rate_limit_resend_verification_window_seconds: int = Field(default=60, gt=0)
    rate_limit_change_password: int = Field(default=5, gt=0)
    rate_limit_change_password_window_seconds: int = Field(default=15 * 60, gt=0)

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("postgresql://", "postgres://")):
            raise ValueError("must start with postgresql://")
        return value

    @field_validator("groq_api_key")
    @classmethod
    def _check_groq_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret == PLACEHOLDER_JWT_SECRET:
            raise ValueError("is still the placeholder from .env.example, set a real random secret")
        if len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"must be at least {MIN_JWT_SECRET_LENGTH} characters long")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list) and not value:
            raise ValueError("must list at least one origin")
        return value

    @field_validator("workspaces_dir", "packages_dir")
    @classmethod
    def _check_writable_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("must be an absolute path")
        try:
            value.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ValueError("cannot be created") from None
        if not value.is_dir() or not os.access(value, os.W_OK | os.X_OK):
            raise ValueError("is not a writable folder")
        probe = value / ".write_check"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            raise ValueError("is not a writable folder") from None
        return value

    @model_validator(mode="after")
    def _check_smtp(self) -> "Settings":
        if self.mailer_mode == "smtp" and not self.smtp_host:
            raise ValueError("SMTP_HOST is required when MAILER_MODE is smtp")
        return self

    def secret_values(self) -> list[str]:
        """Every secret value, so the logger can mask it if it ever shows up in a log line."""
        values = [self.groq_api_key.get_secret_value(), self.jwt_secret.get_secret_value()]
        values.append(self.database_url.get_secret_value())
        if self.smtp_password is not None:
            values.append(self.smtp_password.get_secret_value())
        password = _password_from_url(self.database_url.get_secret_value())
        if password:
            values.append(password)
        return [v for v in values if v]


def _password_from_url(url: str) -> str | None:
    # postgresql://user:password@host/db
    rest = url.split("://", 1)[-1]
    if "@" not in rest:
        return None
    credentials = rest.rsplit("@", 1)[0]
    if ":" not in credentials:
        return None
    return credentials.split(":", 1)[1] or None


def _describe(error: dict) -> str:
    name = str(error["loc"][0]).upper() if error.get("loc") else "SETTINGS"
    if error.get("type") == "missing":
        return f"{name} is missing (required setting)"
    message = error.get("msg", "is invalid")
    message = message.removeprefix("Value error, ")
    return f"{name} {message}" if message.startswith(("must", "is", "cannot")) else f"{name}: {message}"


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Read and check the settings. Raises SettingsError naming every bad setting (never its value)."""
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "; ".join(_describe(error) for error in exc.errors(include_input=False))
        raise SettingsError(f"Invalid configuration: {problems}") from None
