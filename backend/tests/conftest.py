"""Shared test setup.

Uses a REAL PostgreSQL test database (never SQLite). Point TEST_DATABASE_URL at it, for example
postgresql://sangra:<password>@localhost:5432/sangra_test.

Two guards run in every test (B0-S05): every HTTP response body and every log line written by our
handler is searched for the known test secrets; finding one fails the test.
"""

import io
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.core.config import Settings, load_settings
from app.core.logging import setup_logging
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://sangra:sangra_pw_7Hq2xZ@localhost:5432/sangra_test"
)
TEST_JWT_SECRET = "test-jwt-secret-Qm8vR2pL5nX9tY4wZ7bK3cF6"
TEST_GROQ_KEY = "gsk_test_key_never_leak_A1b2C3d4E5"
BACKEND_DIR = Path(__file__).resolve().parent.parent


def _db_password(url: str) -> str | None:
    rest = url.split("://", 1)[-1]
    if "@" not in rest or ":" not in rest.rsplit("@", 1)[0]:
        return None
    return rest.rsplit("@", 1)[0].split(":", 1)[1] or None


KNOWN_SECRETS = [s for s in (TEST_JWT_SECRET, TEST_GROQ_KEY, _db_password(TEST_DATABASE_URL)) if s]


def find_secrets(text: str) -> list[str]:
    return [secret for secret in KNOWN_SECRETS if secret in text]


SETTING_NAMES = [name.upper() for name in Settings.model_fields]


@pytest.fixture
def base_env(tmp_path: Path) -> dict[str, str]:
    """A complete, valid set of settings (as environment variables)."""
    return {
        "APP_MODE": "local",
        "DATABASE_URL": TEST_DATABASE_URL,
        "GROQ_API_KEY": TEST_GROQ_KEY,
        "GROQ_MODEL": "openai/gpt-oss-120b",
        "JWT_SECRET": TEST_JWT_SECRET,
        "FRONTEND_URL": "http://localhost:5173",
        "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
        "WORKSPACES_DIR": str(tmp_path / "workspaces"),
        "PACKAGES_DIR": str(tmp_path / "packages"),
        "DB_POOL_TIMEOUT_SECONDS": "3",
    }


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str]):
    """Builds Settings only from the given environment (no .env file is read)."""

    def build(overrides: dict[str, str] | None = None, unset: tuple[str, ...] = ()) -> Settings:
        env = {**base_env, **(overrides or {})}
        for name in SETTING_NAMES:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            if name not in unset:
                monkeypatch.setenv(name, value)
        return load_settings(env_file=None)

    return build


@pytest.fixture(autouse=True)
def docker_unreachable_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker answers are made predictable: unreachable unless a test replaces the check."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/sangra-test/docker.sock")


@pytest.fixture(autouse=True)
def log_output() -> Iterator[io.StringIO]:
    """Everything our log handler writes during the test. Fails the test if a secret appears."""
    stream = io.StringIO()
    setup_logging("DEBUG", KNOWN_SECRETS, stream=stream)
    yield stream
    leaked = find_secrets(stream.getvalue())
    assert not leaked, "a secret was written to the logs"


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


async def _check_response_for_secrets(response: httpx.Response) -> None:
    body = await response.aread()
    text = body.decode("utf-8", errors="replace") + str(dict(response.headers))
    assert not find_secrets(text), f"a secret appeared in the response of {response.request.url}"


def make_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        event_hooks={"response": [_check_response_for_secrets]},
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as c:
        yield c
