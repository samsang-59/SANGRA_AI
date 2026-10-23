"""Shared test setup.

Uses a REAL PostgreSQL test database (never SQLite). Point TEST_DATABASE_URL at it, for example
postgresql://sangra:<password>@localhost:5432/sangra_test.

Two guards run in every test (B0-S05): every HTTP response body and every log line written by our
handler is searched for the known test secrets; finding one fails the test.
"""

import asyncio
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
        # Fast hashes in tests; the timing tests (B2-S01, C01, L02) use the real default of 12.
        "BCRYPT_ROUNDS": "4",
        # The live stream checks every 50 ms in tests (1 second by default).
        "STREAM_POLL_SECONDS": "0.05",
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
def settings(make_settings, request: pytest.FixtureRequest) -> Settings:
    """Valid test settings. A test can change some with @pytest.mark.settings(NAME="value", ...)."""
    marker = request.node.get_closest_marker("settings")
    return make_settings(dict(marker.kwargs) if marker else None)


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


# --- Database fixtures (B1 onward) ------------------------------------------------------------------

OUR_TABLES = [
    "users", "user_tokens", "projects", "runs", "attempts",
    "file_edits", "run_events", "test_results", "packages",
]


def database_url_for(name: str) -> str:
    """The test URL with another database name (same server, same role)."""
    base, _, _ = TEST_DATABASE_URL.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """The test database with a fresh schema and every migration applied (once per test session)."""
    import psycopg

    from app.migrate import run_migrations

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    run_migrations(TEST_DATABASE_URL)
    from app.agent.workflow import setup_langgraph_tables

    setup_langgraph_tables(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
async def db(migrated_database: str):
    """An autocommit connection to the migrated test database. Every table is emptied first."""
    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(migrated_database, autocommit=True, row_factory=dict_row)
    await conn.execute(f"TRUNCATE {', '.join(OUR_TABLES)} RESTART IDENTITY CASCADE")
    # Run ids start again at 1, so LangGraph's rows (keyed by the run id) are emptied too.
    await conn.execute("TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def empty_database() -> Iterator[str]:
    """A brand new, empty database for this test only (dropped afterwards). Returns its URL."""
    import uuid

    import psycopg

    name = f"sangra_test_tmp_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield database_url_for(name)
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


class FakeClock:
    """Replaces app.core.clock. It follows the real time plus an offset that only advance() changes.

    Following real time matters: the database clock (now()) cannot be faked, and a JWT must never look
    older than a password_changed_at the database just wrote.
    """

    def __init__(self) -> None:
        self.offset = 0.0

    def now(self):
        from datetime import datetime, timedelta, timezone

        return datetime.now(timezone.utc) + timedelta(seconds=self.offset)

    def monotonic(self) -> float:
        import time

        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    from app.core import clock

    fake = FakeClock()
    monkeypatch.setattr(clock, "now", fake.now)
    monkeypatch.setattr(clock, "monotonic", fake.monotonic)
    return fake


class FakeMailer:
    """Keeps every email in memory (B2/B3 tests). fail=True makes sending raise."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_email(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise ConnectionError("fake mailer is down")
        self.sent.append({"to": to, "subject": subject, "body": body})

    def tokens(self, to: str | None = None, page: str | None = None) -> list[str]:
        import re

        found = []
        for mail in self.sent:
            if to is None or mail["to"] == to:
                for link_page, token in re.findall(r"/([a-z-]+)\?token=([A-Za-z0-9_-]+)", mail["body"]):
                    if page is None or page == link_page:
                        found.append(token)
        return found


@pytest.fixture
def mailer(app: FastAPI) -> FakeMailer:
    fake = FakeMailer()
    app.state.mailer = fake
    return fake


class DatabaseProxy:
    """A TCP proxy in front of PostgreSQL, so a test can make the database "go away" and come back."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = 0
        self.up = True
        self._server: asyncio.base_events.Server | None = None
        self._writers: set = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        if not self.up:
            writer.close()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(*self.target)
        except OSError:
            writer.close()
            return
        self._writers.update({writer, upstream_writer})

        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))

    def go_down(self) -> None:
        """Refuses new connections and cuts every open one."""
        self.up = False
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()

    def come_back(self) -> None:
        self.up = True

    async def stop(self) -> None:
        self.go_down()
        if self._server:
            self._server.close()
            await self._server.wait_closed()


@pytest.fixture
async def db_proxy(migrated_database: str):
    from urllib.parse import urlsplit

    parts = urlsplit(migrated_database)
    proxy = DatabaseProxy(parts.hostname or "localhost", parts.port or 5432)
    await proxy.start()
    try:
        yield proxy
    finally:
        await proxy.stop()


def url_through_proxy(url: str, port: int) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    credentials = parts.netloc.rsplit("@", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{credentials}@127.0.0.1:{port}"))


def sandbox_network_extras() -> tuple[dict[str, str], dict[str, str]]:
    """Only on a machine whose network inspects TLS with its own certificate (this cloud sandbox): hands that
    certificate to the test containers so Maven and pip can download. On a normal machine: nothing."""
    ca = Path("/root/.ccr/ca-bundle.crt")
    java_store = Path("/etc/ssl/certs/java/cacerts")
    env: dict[str, str] = {}
    mounts: dict[str, str] = {}
    if ca.exists():
        mounts[str(ca)] = "/etc/sangra-ca.crt"
        env.update({"SSL_CERT_FILE": "/etc/sangra-ca.crt", "PIP_CERT": "/etc/sangra-ca.crt",
                    "REQUESTS_CA_BUNDLE": "/etc/sangra-ca.crt"})
        if java_store.exists():
            mounts[str(java_store)] = "/etc/sangra-java-cacerts"
            env["MAVEN_OPTS"] = ("-Djavax.net.ssl.trustStore=/etc/sangra-java-cacerts "
                                 "-Djavax.net.ssl.trustStorePassword=changeit")
    return env, mounts


@pytest.fixture
def docker_runner(settings):
    from app.runners.test_runner import TestRunner

    env, mounts = sandbox_network_extras()
    return TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix="sangra-test-cache")
