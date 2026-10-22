"""Phase B3: the 8 auth routes through all layers."""

import asyncio
import hashlib
import json
import statistics
import time
import uuid

import pytest
from asgi_lifespan import LifespanManager

from app.core.security import hash_password
from app.main import create_app
from tests.conftest import FakeMailer, find_secrets, make_client, url_through_proxy

PASSWORD = "secret123"
NEW_PASSWORD = "better456"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def register(client, email: str | None = None, password: str = PASSWORD, name: str = "Ann"):
    return await client.post("/api/v1/auth/register", json={"name": name, "email": unique_email() if email is None else email,
                                                               "password": password})


async def registered(client, mailer, email: str | None = None) -> dict:
    """Registers a user; returns its email, JWT, id and the verification token from the email."""
    email = email or unique_email()
    response = await register(client, email)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return {"email": email, "token": data["token"], "id": data["user"]["id"],
            "verify_token": mailer.tokens(to=email, page="verify-email")[-1]}


async def login(client, email: str, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def me(client, token: str):
    return await client.get("/api/v1/auth/me", headers=bearer(token))


async def forgot_and_get_token(client, app, mailer, email: str) -> str:
    response = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert response.status_code == 200
    await app.state.background.drain()
    return mailer.tokens(to=email, page="reset-password")[-1]


async def next_second() -> None:
    """Waits until the wall clock enters a new second (JWT times have whole-second precision, Gaps #1)."""
    await asyncio.sleep(1.02 - (time.time() % 1))


def assert_error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    return body["error"]["message"]


# --- Functional ---------------------------------------------------------------------------------------

async def test_B3_F01_register(client, mailer, db):
    response = await register(client, "Ann.Lee@Example.COM")
    assert response.status_code == 201
    data = response.json()["data"]
    assert set(data) == {"user", "token"}
    assert data["user"]["email"] == "ann.lee@example.com"
    assert data["user"]["is_email_verified"] is False
    assert "password_hash" not in data["user"] and "password" not in json.dumps(data)
    cur = await db.execute("SELECT email FROM users WHERE id = %s", (data["user"]["id"],))
    assert (await cur.fetchone())["email"] == "ann.lee@example.com"
    assert len(mailer.sent) == 1 and mailer.sent[0]["to"] == "ann.lee@example.com"
    assert "http://localhost:5173/verify-email?token=" in mailer.sent[0]["body"]
    assert len(mailer.tokens(page="verify-email")) == 1


async def test_B3_F02_F03_login_and_me(client, mailer, db):
    user = await registered(client, mailer)
    response = await login(client, user["email"])
    assert response.status_code == 200
    token = response.json()["data"]["token"]
    response = await me(client, token)
    assert response.status_code == 200
    assert response.json()["data"]["email"] == user["email"]
    assert response.json()["data"]["id"] == user["id"]


async def test_B3_F04_verify_email(client, mailer, db):
    user = await registered(client, mailer)
    response = await client.post("/api/v1/auth/verify-email", json={"token": user["verify_token"]})
    assert response.status_code == 200
    assert (await me(client, user["token"])).json()["data"]["is_email_verified"] is True
    assert len(mailer.sent) == 1  # no email is sent here


async def test_B3_F05_resend_after_60_seconds(client, mailer, db):
    user = await registered(client, mailer)
    # 61 seconds later (the database clock decides, so the token is made older).
    await db.execute("UPDATE user_tokens SET created_at = created_at - interval '61 seconds' WHERE user_id = %s",
                     (user["id"],))
    response = await client.post("/api/v1/auth/resend-verification", headers=bearer(user["token"]))
    assert response.status_code == 200
    assert len(mailer.sent) == 2
    new_token = mailer.tokens(to=user["email"], page="verify-email")[-1]
    assert new_token != user["verify_token"]
    assert (await client.post("/api/v1/auth/verify-email", json={"token": new_token})).status_code == 200


async def test_B3_F06_forgot_password_for_a_known_email(client, app, mailer, db):
    user = await registered(client, mailer)
    response = await client.post("/api/v1/auth/forgot-password", json={"email": user["email"]})
    assert response.status_code == 200
    await app.state.background.drain()
    reset_mails = [m for m in mailer.sent if "reset-password?token=" in m["body"]]
    assert len(reset_mails) == 1 and reset_mails[0]["to"] == user["email"]


async def test_B3_F07_reset_password(client, app, mailer, db):
    user = await registered(client, mailer)
    token = await forgot_and_get_token(client, app, mailer, user["email"])
    response = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    assert (await login(client, user["email"], NEW_PASSWORD)).status_code == 200
    assert (await login(client, user["email"], PASSWORD)).status_code == 401


async def test_B3_F08_change_password(client, mailer, db):
    user = await registered(client, mailer)
    response = await client.post("/api/v1/auth/change-password", headers=bearer(user["token"]),
                                 json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    assert "token" not in response.json()["data"]  # no new JWT here
    assert (await login(client, user["email"], NEW_PASSWORD)).status_code == 200


async def test_B3_F09_new_user_can_use_the_app_at_once(client, mailer, db):
    user = await registered(client, mailer)
    assert (await me(client, user["token"])).status_code == 200


# --- Failure --------------------------------------------------------------------------------------

async def test_B3_X01_email_already_registered(client, mailer, db):
    await register(client, "dup@example.com")
    assert_error(await register(client, "dup@example.com"), 409, "EMAIL_ALREADY_REGISTERED")
    assert_error(await register(client, "DUP@Example.com"), 409, "EMAIL_ALREADY_REGISTERED")


@pytest.mark.parametrize("email", ["noat.example.com", "a@@b.com", "a b@example.com", "x" * 250 + "@example.com", ""])
async def test_B3_X02_bad_emails(client, mailer, db, email):
    message = assert_error(await register(client, email), 422, "VALIDATION_ERROR")
    assert "email" in message


@pytest.mark.parametrize("password", ["abc1234", "abcdefgh", "12345678", "        ", "a1" * 36 + "b"])
async def test_B3_X03_bad_passwords(client, mailer, db, password):
    message = assert_error(await register(client, password=password), 422, "VALIDATION_ERROR")
    assert "password" in message
    assert password.strip() == "" or password not in message


async def test_B3_X04_names(client, mailer, db):
    for bad in ["", "   ", "x" * 101]:
        assert_error(await register(client, name=bad), 422, "VALIDATION_ERROR")
    for good in ["Ana 😀", "Søren Ünal 王小明", "x" * 100]:
        response = await register(client, name=good)
        assert response.status_code == 201 and response.json()["data"]["user"]["name"] == good
    trimmed = await register(client, name="  Bob  ")
    assert trimmed.json()["data"]["user"]["name"] == "Bob"


async def test_B3_X05_missing_extra_and_wrong_types(client, mailer, db):
    url = "/api/v1/auth/register"
    assert_error(await client.post(url, json={}), 422, "VALIDATION_ERROR")
    assert_error(await client.post(url, json={"name": "A", "email": unique_email()}), 422, "VALIDATION_ERROR")
    assert_error(await client.post(url, json={"name": "A", "email": 12345, "password": PASSWORD}), 422,
                 "VALIDATION_ERROR")
    assert_error(await client.post(url, json={"name": "A", "email": unique_email(), "password": ["a", 1]}), 422,
                 "VALIDATION_ERROR")
    assert_error(await client.post(url, json=["not", "an", "object"]), 422, "VALIDATION_ERROR")
    extra = await client.post(url, json={"name": "A", "email": unique_email(), "password": PASSWORD,
                                         "is_email_verified": True, "id": 1, "role": "admin"})
    assert extra.status_code == 201
    assert extra.json()["data"]["user"]["is_email_verified"] is False  # the extra field was ignored
    for route in ["login", "verify-email", "forgot-password", "reset-password"]:
        response = await client.post(f"/api/v1/auth/{route}", json={"email": 5, "token": 7, "password": None})
        assert response.status_code == 422


async def test_B3_X06_mailer_fails_during_register(client, app, db, log_output):
    app.state.mailer = FakeMailer(fail=True)
    response = await register(client)
    assert response.status_code == 201
    assert (await me(client, response.json()["data"]["token"])).status_code == 200
    assert "Sending the email" in log_output.getvalue() and "was not sent" in log_output.getvalue()


async def test_B3_X07_unknown_email_and_wrong_password_look_the_same(client, mailer, db):
    user = await registered(client, mailer)
    unknown = await login(client, unique_email("nobody"))
    wrong = await login(client, user["email"], "wrong-pass-1")
    assert assert_error(unknown, 401, "INVALID_CREDENTIALS") == assert_error(wrong, 401, "INVALID_CREDENTIALS")
    assert unknown.json() == wrong.json()


async def test_B3_X08_unverified_user_can_log_in(client, mailer, db):
    user = await registered(client, mailer)
    assert (await login(client, user["email"])).status_code == 200


async def test_B3_X09_eleventh_login_in_15_minutes(client, mailer, db):
    user = await registered(client, mailer)
    for _ in range(10):
        assert (await login(client, user["email"], "wrong-pass-1")).status_code == 401
    assert_error(await login(client, user["email"]), 429, "RATE_LIMITED")


async def test_B3_X10_bad_verify_tokens(client, app, mailer, db):
    user = await registered(client, mailer)
    url = "/api/v1/auth/verify-email"
    random_token = "A" * 43
    assert_error(await client.post(url, json={"token": random_token}), 400, "TOKEN_INVALID_OR_EXPIRED")
    # Expired: 24 hours and 1 second old (the database clock decides).
    await db.execute("UPDATE user_tokens SET created_at = now() - interval '24 hours 1 second', "
                     "expires_at = now() - interval '1 second' WHERE user_id = %s", (user["id"],))
    assert_error(await client.post(url, json={"token": user["verify_token"]}), 400, "TOKEN_INVALID_OR_EXPIRED")
    # Used: verify a fresh token twice.
    other = await registered(client, mailer)
    assert (await client.post(url, json={"token": other["verify_token"]})).status_code == 200
    assert_error(await client.post(url, json={"token": other["verify_token"]}), 400, "TOKEN_INVALID_OR_EXPIRED")
    # A reset token on the verify route.
    reset_token = await forgot_and_get_token(client, app, mailer, user["email"])
    assert_error(await client.post(url, json={"token": reset_token}), 400, "TOKEN_INVALID_OR_EXPIRED")
    # Wrong length or characters.
    for bad in ["short", "A" * 44, "A" * 42 + "!", ""]:
        assert_error(await client.post(url, json={"token": bad}), 422, "VALIDATION_ERROR")


async def test_B3_X11_resend_rules(client, mailer, db):
    user = await registered(client, mailer)
    url = "/api/v1/auth/resend-verification"
    message = assert_error(await client.post(url, headers=bearer(user["token"])), 429, "TOO_SOON")
    assert "Try again in" in message
    assert_error(await client.post(url), 401, "UNAUTHORIZED")

    verified = await registered(client, mailer)
    await client.post("/api/v1/auth/verify-email", json={"token": verified["verify_token"]})
    response = await client.post(url, headers=bearer(verified["token"]))
    assert response.status_code == 200 and "already verified" in response.json()["data"]["message"]


@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000")
async def test_B3_X12_forgot_password_for_an_unknown_email(client, app, mailer, db):
    user = await registered(client, mailer)
    known = await client.post("/api/v1/auth/forgot-password", json={"email": user["email"]})
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": unique_email("nobody")})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    await app.state.background.drain()
    assert len(mailer.sent) == 2  # the verification email and ONE reset email (for the known address)

    async def timed(email):
        started = time.perf_counter()
        await client.post("/api/v1/auth/forgot-password", json={"email": email})
        return (time.perf_counter() - started) * 1000

    known_ms = [await timed(user["email"]) for _ in range(20)]
    unknown_ms = [await timed(unique_email("nobody")) for _ in range(20)]
    await app.state.background.drain()
    gap = abs(statistics.median(known_ms) - statistics.median(unknown_ms))
    print(f"\nB3-X12: known {statistics.median(known_ms):.1f} ms, unknown {statistics.median(unknown_ms):.1f} ms")
    assert gap < 10


async def test_B3_X13_fourth_forgot_password_in_an_hour(client, mailer, db):
    email = unique_email()
    for _ in range(3):
        assert (await client.post("/api/v1/auth/forgot-password", json={"email": email})).status_code == 200
    assert_error(await client.post("/api/v1/auth/forgot-password", json={"email": email}), 429, "RATE_LIMITED")


@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000")
async def test_B3_X14_bad_reset_attempts_keep_the_old_password(client, app, mailer, db):
    user = await registered(client, mailer)
    url = "/api/v1/auth/reset-password"
    expired = await forgot_and_get_token(client, app, mailer, user["email"])
    await db.execute("UPDATE user_tokens SET created_at = now() - interval '30 minutes 1 second', "
                     "expires_at = now() - interval '1 second' WHERE token_type = 'password_reset'")
    assert_error(await client.post(url, json={"token": expired, "new_password": NEW_PASSWORD}), 400,
                 "TOKEN_INVALID_OR_EXPIRED")
    assert_error(await client.post(url, json={"token": user["verify_token"], "new_password": NEW_PASSWORD}), 400,
                 "TOKEN_INVALID_OR_EXPIRED")
    fresh = await forgot_and_get_token(client, app, mailer, user["email"])
    assert_error(await client.post(url, json={"token": fresh, "new_password": "weak"}), 422, "VALIDATION_ERROR")
    assert (await login(client, user["email"])).status_code == 200  # the old password still works
    assert (await client.post(url, json={"token": fresh, "new_password": NEW_PASSWORD})).status_code == 200
    assert_error(await client.post(url, json={"token": fresh, "new_password": "another789"}), 400,
                 "TOKEN_INVALID_OR_EXPIRED")  # used
    assert (await login(client, user["email"], NEW_PASSWORD)).status_code == 200


@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000")
async def test_B3_X15_other_reset_tokens_die_after_a_reset(client, app, mailer, db):
    user = await registered(client, mailer)
    first = await forgot_and_get_token(client, app, mailer, user["email"])
    second = await forgot_and_get_token(client, app, mailer, user["email"])
    url = "/api/v1/auth/reset-password"
    assert (await client.post(url, json={"token": second, "new_password": NEW_PASSWORD})).status_code == 200
    assert_error(await client.post(url, json={"token": first, "new_password": "another789"}), 400,
                 "TOKEN_INVALID_OR_EXPIRED")
    cur = await db.execute("SELECT count(*) AS n FROM user_tokens WHERE user_id = %s AND token_type = 'password_reset' "
                           "AND used_at IS NULL", (user["id"],))
    assert (await cur.fetchone())["n"] == 0


async def test_B3_X16_change_password_errors(client, mailer, db):
    user = await registered(client, mailer)
    url = "/api/v1/auth/change-password"
    headers = bearer(user["token"])
    assert_error(await client.post(url, headers=headers, json={"old_password": "wrong-pass-1", "new_password": NEW_PASSWORD}),
                 400, "WRONG_OLD_PASSWORD")
    assert_error(await client.post(url, headers=headers, json={"old_password": PASSWORD, "new_password": PASSWORD}),
                 400, "NEW_PASSWORD_SAME_AS_OLD")
    assert_error(await client.post(url, headers=headers, json={"old_password": PASSWORD, "new_password": "weak"}),
                 422, "VALIDATION_ERROR")
    assert_error(await client.post(url, json={"old_password": PASSWORD, "new_password": NEW_PASSWORD}),
                 401, "UNAUTHORIZED")
    assert (await me(client, user["token"])).status_code == 200  # the user is still logged in


@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000")
async def test_B3_X17_old_jwt_dies_after_a_change_or_a_reset(client, app, mailer, db):
    user = await registered(client, mailer)
    await next_second()
    await client.post("/api/v1/auth/change-password", headers=bearer(user["token"]),
                      json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert_error(await me(client, user["token"]), 401, "UNAUTHORIZED")

    fresh_jwt = (await login(client, user["email"], NEW_PASSWORD)).json()["data"]["token"]
    assert (await me(client, fresh_jwt)).status_code == 200
    reset_token = await forgot_and_get_token(client, app, mailer, user["email"])
    await next_second()
    await client.post("/api/v1/auth/reset-password", json={"token": reset_token, "new_password": "third789x"})
    assert_error(await me(client, fresh_jwt), 401, "UNAUTHORIZED")


async def test_B3_X18_sixth_change_password_in_15_minutes(client, mailer, db):
    user = await registered(client, mailer)
    body = {"old_password": "wrong-pass-1", "new_password": NEW_PASSWORD}
    for _ in range(5):
        assert (await client.post("/api/v1/auth/change-password", headers=bearer(user["token"]), json=body)).status_code == 400
    assert_error(await client.post("/api/v1/auth/change-password", headers=bearer(user["token"]), json=body),
                 429, "RATE_LIMITED")


# --- Security -------------------------------------------------------------------------------------

@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000")
async def test_B3_S01_S02_hashes_and_raw_tokens_never_leave(client, app, mailer, db):
    responses = []
    email = unique_email()
    for response in [await register(client, email), await login(client, email)]:
        responses.append(response)
    token = responses[0].json()["data"]["token"]
    responses.append(await me(client, token))
    responses.append(await client.post("/api/v1/auth/resend-verification", headers=bearer(token)))
    responses.append(await client.post("/api/v1/auth/forgot-password", json={"email": email}))
    await app.state.background.drain()
    raw_tokens = mailer.tokens(to=email)
    responses.append(await client.post("/api/v1/auth/verify-email", json={"token": raw_tokens[0]}))
    responses.append(await client.post("/api/v1/auth/reset-password",
                                       json={"token": raw_tokens[-1], "new_password": NEW_PASSWORD}))

    cur = await db.execute("SELECT password_hash FROM users WHERE email = %s", (email,))
    password_hash = (await cur.fetchone())["password_hash"]
    cur = await db.execute("SELECT token_hash FROM user_tokens")
    token_hashes = [row["token_hash"] for row in await cur.fetchall()]
    assert len(raw_tokens) == 2 and len(token_hashes) == 2
    for response in responses:
        for secret in [password_hash, *token_hashes, *raw_tokens]:
            assert secret not in response.text

    # B3-S02: the database holds only the token HASH, never the raw token.
    assert sorted(token_hashes) == sorted(hashlib.sha256(t.encode()).hexdigest() for t in raw_tokens)
    for table in ["users", "user_tokens"]:
        cur = await db.execute(f"SELECT row_to_json(t)::text AS row FROM {table} t")
        dump = " ".join(r["row"] for r in await cur.fetchall())
        assert not any(t in dump for t in raw_tokens)


async def test_B3_S03_a_jwt_only_shows_its_own_user(client, mailer, db):
    a, b = await registered(client, mailer), await registered(client, mailer)
    assert (await me(client, a["token"])).json()["data"]["email"] == a["email"]
    assert (await me(client, b["token"])).json()["data"]["email"] == b["email"]


async def test_B3_S04_sql_and_script_text_stays_plain(client, mailer, db):
    name = "<script>alert(1)</script> ' OR 1=1 --"
    email = "o'brien+test@example.com"
    response = await register(client, email, name=name)
    assert response.status_code == 201
    assert response.json()["data"]["user"]["name"] == name
    login_response = await login(client, email)
    assert login_response.status_code == 200
    assert (await me(client, login_response.json()["data"]["token"])).json()["data"]["name"] == name
    assert_error(await login(client, "' OR 1=1 --@example.com"), 422, "VALIDATION_ERROR")
    cur = await db.execute("SELECT count(*) AS n FROM users")
    assert (await cur.fetchone())["n"] == 1


@pytest.mark.slow
async def test_B3_S05_guessing_verify_tokens(client, mailer, db):
    import secrets

    user = await registered(client, mailer)
    statuses = set()
    for _ in range(1000):
        response = await client.post("/api/v1/auth/verify-email", json={"token": secrets.token_urlsafe(32)})
        statuses.add(response.status_code)
    assert statuses == {400}
    assert (await me(client, user["token"])).json()["data"]["is_email_verified"] is False


# --- Concurrency ----------------------------------------------------------------------------------

@pytest.mark.settings(RATE_LIMIT_REGISTER="1000")
async def test_B3_C01_50_parallel_registers_with_one_email(client, mailer, db):
    responses = await asyncio.gather(*(register(client, "same@example.com") for _ in range(50)))
    statuses = [r.status_code for r in responses]
    assert statuses.count(201) == 1 and statuses.count(409) == 49


async def test_B3_C02_same_verify_link_20_times_at_once(client, mailer, db):
    user = await registered(client, mailer)
    responses = await asyncio.gather(*(
        client.post("/api/v1/auth/verify-email", json={"token": user["verify_token"]}) for _ in range(20)))
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1 and statuses.count(400) == 19


async def test_B3_C03_same_reset_token_20_times_at_once(client, app, mailer, db):
    user = await registered(client, mailer)
    token = await forgot_and_get_token(client, app, mailer, user["email"])
    responses = await asyncio.gather(*(
        client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": f"parallel{n}x"})
        for n in range(20)))
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1 and statuses.count(400) == 19


async def test_B3_C04_two_password_changes_at_once(client, mailer, db):
    user = await registered(client, mailer)
    responses = await asyncio.gather(*(
        client.post("/api/v1/auth/change-password", headers=bearer(user["token"]),
                    json={"old_password": PASSWORD, "new_password": new}) for new in ("first111", "second222")))
    assert all(r.status_code in (200, 401) for r in responses)
    assert any(r.status_code == 200 for r in responses)
    works = [(await login(client, user["email"], p)).status_code == 200 for p in ("first111", "second222")]
    assert works.count(True) == 1  # one consistent final password


@pytest.mark.settings(RATE_LIMIT_FORGOT_PASSWORD="1000", RATE_LIMIT_LOGIN="1000")
async def test_B3_C05_reset_and_login_at_the_same_moment(client, app, mailer, db):
    for _ in range(5):
        user = await registered(client, mailer)
        token = await forgot_and_get_token(client, app, mailer, user["email"])
        results = await asyncio.gather(
            client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}),
            login(client, user["email"], PASSWORD),
            login(client, user["email"], NEW_PASSWORD),
        )
        assert results[0].status_code == 200
        # Login sees the old or the new password, never an error.
        assert results[1].status_code in (200, 401) and results[2].status_code in (200, 401)


# --- Recovery -------------------------------------------------------------------------------------

async def test_B3_R01_database_goes_away_during_a_request(make_settings, migrated_database, db, db_proxy):
    settings = make_settings({"DATABASE_URL": url_through_proxy(migrated_database, db_proxy.port),
                              "DB_POOL_TIMEOUT_SECONDS": "2"})
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        app.state.mailer = FakeMailer()
        user = await registered(client, app.state.mailer)
        assert (await me(client, user["token"])).status_code == 200

        db_proxy.go_down()
        assert_error(await me(client, user["token"]), 500, "INTERNAL_ERROR")
        assert_error(await login(client, user["email"]), 500, "INTERNAL_ERROR")
        await asyncio.sleep(6)  # a longer outage: with doubling retries the pool would now wait 8+ seconds
        assert_error(await me(client, user["token"]), 500, "INTERNAL_ERROR")

        db_proxy.come_back()
        response = await me(client, user["token"])
        assert response.status_code == 200, response.text
        assert (await login(client, user["email"])).status_code == 200


def test_B3_R01_pool_retry_setting_is_in_place():
    from psycopg_pool.base import AttemptWithBackoff

    import app.core.db  # noqa: F401  (applies the setting)

    assert AttemptWithBackoff.DELAY_BACKOFF == 1.0
    assert AttemptWithBackoff.INITIAL_DELAY <= 1.0


# --- Load -----------------------------------------------------------------------------------------

async def _insert_users(db, count: int, password_hash: str) -> list[str]:
    emails = [unique_email(f"load{n}") for n in range(count)]
    async with db.cursor() as cur:
        await cur.executemany("INSERT INTO users (name, email, password_hash) VALUES ('Load', %s, %s)",
                              [(e, password_hash) for e in emails])
    return emails


@pytest.mark.slow
@pytest.mark.settings(RATE_LIMIT_LOGIN="1000", BCRYPT_ROUNDS="12")
async def test_B3_L01_50_users_log_in_at_the_same_time(client, db):
    emails = await _insert_users(db, 50, await hash_password(PASSWORD, 12))
    started = time.perf_counter()
    logins = asyncio.gather(*(login(client, e) for e in emails))
    await asyncio.sleep(0.05)
    slowest_health = 0.0
    while not logins.done():
        t = time.perf_counter()
        assert (await client.get("/api/v1/health")).status_code == 200
        slowest_health = max(slowest_health, time.perf_counter() - t)
        await asyncio.sleep(0.05)
    results = await logins
    total = time.perf_counter() - started
    print(f"\nB3-L01: 50 logins at once took {total:.2f} s; slowest /health meanwhile {slowest_health * 1000:.1f} ms")
    assert [r.status_code for r in results] == [200] * 50
    assert slowest_health < 0.2


@pytest.mark.slow
async def test_B3_L02_1000_sequential_me_calls(client, mailer, db):
    user = await registered(client, mailer)
    durations = []
    for _ in range(1000):
        started = time.perf_counter()
        assert (await me(client, user["token"])).status_code == 200
        durations.append((time.perf_counter() - started) * 1000)
    durations.sort()
    print(f"\nB3-L02: /auth/me p50 {durations[500]:.2f} ms, p95 {durations[949]:.2f} ms")
    assert durations[949] < 50


@pytest.mark.slow
async def test_B3_L03_500_users_then_500_parallel_me_calls(client, app, db):
    emails = await _insert_users(db, 500, "$2b$04$not-used-in-this-test")
    cur = await db.execute("SELECT id FROM users WHERE email = ANY(%s)", (emails,))
    from app.core.security import create_access_token
    from tests.conftest import TEST_JWT_SECRET

    tokens = [create_access_token(row["id"], TEST_JWT_SECRET, 24) for row in await cur.fetchall()]
    started = time.perf_counter()
    results = await asyncio.gather(*(me(client, t) for t in tokens))
    print(f"\nB3-L03: 500 parallel /auth/me took {(time.perf_counter() - started) * 1000:.0f} ms; "
          f"pool stats {app.state.db_pool.get_stats().get('requests_errors', 0)} errors")
    assert [r.status_code for r in results] == [200] * 500
    assert app.state.db_pool.get_stats().get("requests_errors", 0) == 0
    assert not find_secrets(" ".join(r.text for r in results))
