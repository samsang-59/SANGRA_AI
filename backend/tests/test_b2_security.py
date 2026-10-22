"""Phase B2: security helpers (bcrypt, JWT, email tokens), mailer, rate limiter, login check."""

import asyncio
import ast
import base64
import io
import json
import statistics
import time
import tracemalloc

import bcrypt
import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ValidationError

from app.core import security
from app.core.auth import get_current_user
from app.core.config import PLACEHOLDER_JWT_SECRET, SettingsError
from app.core.mailer import ConsoleMailer, deliver
from app.core.rate_limit import RateLimiter, Rule, rate_limited_response
from app.core.security import (
    DummyHash,
    InvalidTokenError,
    check_login_password,
    create_access_token,
    decode_access_token,
    hash_email_token,
    hash_password,
    new_email_token,
    verify_password,
)
from app.validators.common import LoginPassword, NewPassword
from tests.conftest import BACKEND_DIR, TEST_JWT_SECRET, find_secrets
from tests.factories import insert_user

HOURS_24 = 24 * 3600


def add_test_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/_test")

    @router.get("/me")
    async def me(user: dict = Depends(get_current_user)) -> dict:
        return {"data": {"id": user["id"], "email": user["email"]}}

    @router.post("/limited")
    async def limited(request: Request, email: str):
        state = request.app.state
        result = state.rate_limiter.check_all([
            (state.rate_rules.login, f"ip:{request.client.host}"),
            (state.rate_rules.login, f"email:{email}"),
        ])
        if not result.allowed:
            return rate_limited_response(result)
        return {"data": "ok"}

    app.include_router(router)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def tamper(token: str, part: int) -> str:
    pieces = token.split(".")
    segment = pieces[part]
    pieces[part] = segment[:-2] + ("A" if segment[-2] != "A" else "B") + segment[-1]
    return ".".join(pieces)


@pytest.fixture
async def user_and_token(app, db, fake_clock):
    user = await insert_user(db, password_changed_at=fake_clock.now().replace(microsecond=0))
    add_test_routes(app)
    token = create_access_token(user["id"], TEST_JWT_SECRET, 24)
    return user, token


# --- Functional ---------------------------------------------------------------------------------------

async def test_B2_F01_hash_and_check_password():
    hashed = await hash_password("correct horse 1", rounds=4)
    assert hashed.startswith("$2b$04$") and "correct" not in hashed
    assert await verify_password("correct horse 1", hashed) is True
    assert await verify_password("wrong horse 1", hashed) is False
    assert await verify_password("x", "not-a-bcrypt-hash") is False


def test_B2_F02_jwt_claims(fake_clock):
    token = create_access_token(42, TEST_JWT_SECRET, 24)
    claims = decode_access_token(token, TEST_JWT_SECRET)
    issued = int(fake_clock.now().timestamp())
    assert (claims.user_id, claims.issued_at, claims.expires_at) == (42, issued, issued + HOURS_24)


def test_B2_F03_email_tokens():
    tokens = [new_email_token() for _ in range(1000)]
    assert len(set(tokens)) == 1000
    for token in tokens:
        assert len(token) == security.EMAIL_TOKEN_LENGTH
        assert len(base64.urlsafe_b64decode(token + "=")) >= 32  # at least 32 random bytes
    assert hash_email_token(tokens[0]) == hash_email_token(tokens[0])
    assert len(hash_email_token(tokens[0])) == 64 and hash_email_token(tokens[0]) != hash_email_token(tokens[1])


async def test_B2_F04_console_mailer_prints_text_and_link():
    out = io.StringIO()
    link = "http://localhost:5173/verify-email?token=abc123"
    await ConsoleMailer(out).send_email("a@example.com", "Verify your email", f"Open this link: {link}")
    printed = out.getvalue()
    assert "a@example.com" in printed and "Verify your email" in printed and link in printed


async def test_B2_F04_console_mailer_is_the_default(app):
    assert isinstance(app.state.mailer, ConsoleMailer)


async def test_B2_F05_valid_jwt_hands_the_user_to_the_route(client, user_and_token):
    user, token = user_and_token
    response = await client.get("/api/v1/_test/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {"data": {"id": user["id"], "email": user["email"]}}


# --- Failure: JWT ---------------------------------------------------------------------------------

async def assert_401(client, headers) -> None:
    response = await client.get("/api/v1/_test/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_B2_X01_edited_payload_or_signature(client, user_and_token):
    _, token = user_and_token
    await assert_401(client, bearer(tamper(token, 1)))
    await assert_401(client, bearer(tamper(token, 2)))
    # A payload changed to another user id, keeping the old signature.
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = str(int(claims["sub"]) + 1)
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    await assert_401(client, bearer(f"{header}.{forged}.{signature}"))


async def test_B2_X02_signed_with_another_secret(client, user_and_token):
    user, _ = user_and_token
    await assert_401(client, bearer(create_access_token(user["id"], "another-secret-of-at-least-32-characters!", 24)))


async def test_B2_X03_algorithm_none_or_other(client, user_and_token, fake_clock):
    user, _ = user_and_token
    now = int(fake_clock.now().timestamp())
    claims = {"sub": str(user["id"]), "iat": now, "exp": now + 3600}
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    await assert_401(client, bearer(unsigned))
    await assert_401(client, bearer(jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS512")))
    await assert_401(client, bearer(jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS384")))


async def test_B2_X04_expired_jwt(client, user_and_token, fake_clock):
    _, token = user_and_token
    fake_clock.advance(HOURS_24 - 1)
    assert (await client.get("/api/v1/_test/me", headers=bearer(token))).status_code == 200
    fake_clock.advance(2)  # 24 hours and 1 second
    await assert_401(client, bearer(token))


async def test_B2_X05_bad_authorization_headers(client, user_and_token):
    _, token = user_and_token
    for headers in [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"},
                    {"Authorization": f"Token {token}"}, {"Authorization": f"Bearer {token} extra"},
                    {"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Bearer " + "x" * 5000}]:
        await assert_401(client, headers)
    # Two Authorization headers.
    response = await client.get("/api/v1/_test/me", headers=[("Authorization", f"Bearer {token}"),
                                                             ("Authorization", f"Bearer {token}")])
    assert response.status_code == 401


async def test_B2_X06_user_was_deleted(client, user_and_token, db):
    user, token = user_and_token
    await db.execute("DELETE FROM users WHERE id = %s", (user["id"],))
    await assert_401(client, bearer(token))


async def test_B2_X07_jwt_issued_before_password_change(client, user_and_token, db, fake_clock):
    user, token = user_and_token
    fake_clock.advance(10)
    await db.execute("UPDATE users SET password_changed_at = %s WHERE id = %s", (fake_clock.now(), user["id"]))
    await assert_401(client, bearer(token))


async def test_B2_X08_jwt_in_the_same_second_after_a_change(client, user_and_token, db, fake_clock):
    user, _ = user_and_token
    fake_clock.advance(10)
    old_token = create_access_token(user["id"], TEST_JWT_SECRET, 24)  # 1 second before the change
    fake_clock.advance(1)
    changed_at = fake_clock.now().replace(microsecond=734_512)  # the database keeps microseconds
    await db.execute("UPDATE users SET password_changed_at = %s WHERE id = %s", (changed_at, user["id"]))
    new_token = create_access_token(user["id"], TEST_JWT_SECRET, 24)  # same second, right after
    assert (await client.get("/api/v1/_test/me", headers=bearer(new_token))).status_code == 200
    await assert_401(client, bearer(old_token))


# --- Failure: passwords ---------------------------------------------------------------------------

class NewPasswordBody(BaseModel):
    password: NewPassword


class LoginBody(BaseModel):
    password: LoginPassword


def test_B2_X09_password_byte_limits(monkeypatch):
    NewPasswordBody(password="a1" * 36)  # exactly 72 bytes
    with pytest.raises(ValidationError, match="72 bytes"):
        NewPasswordBody(password="a1" * 36 + "b")  # 73 bytes
    NewPasswordBody(password="é1" * 24)  # 48 characters, 72 bytes (é is 2 bytes)
    with pytest.raises(ValidationError, match="72 bytes"):
        NewPasswordBody(password="é1" * 24 + "é")  # 49 characters, 74 bytes
    with pytest.raises(ValidationError):
        NewPasswordBody(password="")
    with pytest.raises(ValidationError):
        LoginBody(password="")

    calls = []
    monkeypatch.setattr(bcrypt, "hashpw", lambda *a: calls.append(a))
    started = time.perf_counter()
    with pytest.raises(ValidationError, match="72 bytes"):
        NewPasswordBody(password="a1" * 512 * 1024)  # 1 MB
    assert time.perf_counter() - started < 0.1
    assert calls == []  # refused before bcrypt could run


async def test_B2_X09_long_password_at_login_never_matches(monkeypatch):
    hashed = await hash_password("a1" * 36, rounds=4)
    assert await verify_password("a1" * 36 + "zzz", hashed) is False
    assert await verify_password("a1" * 512 * 1024, hashed) is False


# --- Failure: rate limiter ------------------------------------------------------------------------

async def test_B2_X10_X11_login_limit_and_window(client, user_and_token, fake_clock):
    for _ in range(10):
        assert (await client.post("/api/v1/_test/limited", params={"email": "a@example.com"})).status_code == 200
    eleventh = await client.post("/api/v1/_test/limited", params={"email": "a@example.com"})
    assert eleventh.status_code == 429
    body = eleventh.json()["error"]
    assert body["code"] == "RATE_LIMITED" and "Try again in 900 seconds" in body["message"]
    assert eleventh.headers["Retry-After"] == "900"

    fake_clock.advance(15 * 60)  # B2-X11: after the window passes
    assert (await client.post("/api/v1/_test/limited", params={"email": "a@example.com"})).status_code == 200


def test_B2_X12_different_ips_and_emails_are_counted_separately(fake_clock):
    limiter = RateLimiter()
    rule = Rule("login", 10, 900)
    for _ in range(10):
        assert limiter.check_all([(rule, "ip:1.1.1.1"), (rule, "email:a@x.com")]).allowed
    assert not limiter.check_all([(rule, "ip:1.1.1.1"), (rule, "email:b@x.com")]).allowed  # same IP
    assert not limiter.check_all([(rule, "ip:2.2.2.2"), (rule, "email:a@x.com")]).allowed  # same email
    assert limiter.check_all([(rule, "ip:2.2.2.2"), (rule, "email:b@x.com")]).allowed
    # Other rules never share counts.
    assert limiter.hit(Rule("register", 5, 3600), "ip:1.1.1.1").allowed


async def test_B2_X13_failing_mailer_does_not_escape(log_output):
    class BrokenMailer:
        async def send_email(self, to, subject, body):
            raise ConnectionRefusedError("smtp down")

    secret_link = "http://localhost:5173/verify-email?token=SECRET_TOKEN_IN_BODY"
    assert await deliver(BrokenMailer(), "a@example.com", "Verify your email", secret_link) is False
    logs = log_output.getvalue()
    assert "Verify your email" in logs and "ConnectionRefusedError" in logs
    assert "SECRET_TOKEN_IN_BODY" not in logs  # the body (with the token) is never logged


def test_B2_X13_smtp_mode_needs_a_host(make_settings):
    with pytest.raises(SettingsError, match="SMTP_HOST"):
        make_settings({"MAILER_MODE": "smtp"})
    make_settings({"MAILER_MODE": "smtp", "SMTP_HOST": "smtp.example.com"})


# --- Security -------------------------------------------------------------------------------------

async def test_B2_S01_unknown_email_and_wrong_password_take_the_same_time():
    rounds = 12
    dummy = DummyHash(rounds)
    real_hash = await hash_password("right password 1", rounds)
    await dummy.get()

    async def timed(password_hash):
        started = time.perf_counter()
        assert await check_login_password("wrong password 1", password_hash, dummy) is False
        return time.perf_counter() - started

    unknown = [await timed(None) for _ in range(50)]
    wrong = [await timed(real_hash) for _ in range(50)]
    ratio = statistics.mean(unknown) / statistics.mean(wrong)
    print(f"\nB2-S01: unknown email {statistics.mean(unknown)*1000:.1f} ms, "
          f"wrong password {statistics.mean(wrong)*1000:.1f} ms (ratio {ratio:.2f})")
    assert 0.8 < ratio < 1.25


def test_B2_S02_tokens_come_from_secrets_not_random():
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "random" for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert node.module != "random", path
    source = (BACKEND_DIR / "app/core/security.py").read_text()
    assert "secrets.token_urlsafe" in source


def test_B2_S03_default_or_short_jwt_secret_refused(make_settings):
    for bad in (PLACEHOLDER_JWT_SECRET, "short", "x" * 31):
        with pytest.raises(SettingsError, match="JWT_SECRET"):
            make_settings({"JWT_SECRET": bad})
    make_settings({"JWT_SECRET": "x" * 32})


async def test_B2_S04_jwt_holds_no_email_or_password_hash(db):
    user = await insert_user(db)
    token = create_access_token(user["id"], TEST_JWT_SECRET, 24)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {"sub", "iat", "exp"}
    assert user["email"] not in token and user["password_hash"] not in json.dumps(claims)
    assert not find_secrets(json.dumps(claims))


# --- Concurrency ----------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B2_C01_bcrypt_does_not_block_the_event_loop(client):
    hashing = asyncio.gather(*(hash_password(f"password {n}", 12) for n in range(20)))
    await asyncio.sleep(0.05)  # the hashes are running now
    slowest = 0.0
    for _ in range(10):
        started = time.perf_counter()
        assert (await client.get("/api/v1/health")).status_code == 200
        slowest = max(slowest, time.perf_counter() - started)
    hashes = await hashing
    print(f"\nB2-C01: slowest /health during 20 hashes: {slowest * 1000:.1f} ms")
    assert len(set(hashes)) == 20
    assert slowest < 0.2


async def test_B2_C02_parallel_hits_on_one_key_are_exact(fake_clock):
    limiter = RateLimiter()
    rule = Rule("login", 50, 900)

    async def one():
        await asyncio.sleep(0)
        return limiter.hit(rule, "ip:9.9.9.9").allowed

    results = await asyncio.gather(*(one() for _ in range(100)))
    assert results.count(True) == 50 and results.count(False) == 50

    big = Rule("register", 1000, 900)
    async def counted():
        await asyncio.sleep(0)
        return limiter.hit(big, "k")

    results = await asyncio.gather(*(counted() for _ in range(100)))
    assert all(r.allowed for r in results)
    assert len(limiter._hits["register:k"]) == 100  # every request counted exactly once


# --- Load -----------------------------------------------------------------------------------------

def test_B2_L01_memory_returns_after_the_window(fake_clock):
    limiter = RateLimiter()
    rule = Rule("login", 10, 900)
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for n in range(10_000):
        limiter.hit(rule, f"ip:10.0.{n // 256}.{n % 256}")
    peak = tracemalloc.get_traced_memory()[0]
    assert limiter.key_count() == 10_000
    fake_clock.advance(901)
    limiter.hit(rule, "ip:new")  # the next call sweeps the old entries
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    print(f"\nB2-L01: 10,000 keys used {(peak - before) / 1024:.0f} KiB; after the sweep {(after - before) / 1024:.0f} KiB")
    assert limiter.key_count() == 1
    assert after - before < (peak - before) * 0.1


@pytest.mark.slow
async def test_B2_L02_100_hashes_in_parallel():
    started = time.perf_counter()
    hashes = await asyncio.gather(*(hash_password(f"password {n}", 12) for n in range(100)))
    seconds = time.perf_counter() - started
    print(f"\nB2-L02: 100 bcrypt hashes (cost 12) in parallel took {seconds:.2f} s "
          f"= about {100 / seconds:.0f} logins per second on this machine")
    assert len(set(hashes)) == 100


def test_B2_decode_rejects_garbage():
    for bad in ["", "a.b.c", "x" * 100]:
        with pytest.raises(InvalidTokenError):
            decode_access_token(bad, TEST_JWT_SECRET)
