"""Settings (Layer 7, item 1).

One place that reads the .env file. Checked at startup: the app refuses to start with a clear
message if something is missing or wrong. Secrets are kept as SecretStr so they never show up in
a repr or a log line, and validation messages never repeat the bad value.
"""

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
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

    def secret_values(self) -> list[str]:
        """Every secret value, so the logger can mask it if it ever shows up in a log line."""
        values = [self.groq_api_key.get_secret_value(), self.jwt_secret.get_secret_value()]
        values.append(self.database_url.get_secret_value())
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
