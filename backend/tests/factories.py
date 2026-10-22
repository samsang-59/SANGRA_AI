"""Helpers that insert one valid row per table (raw, parameterised SQL; tests only)."""

import uuid
from datetime import datetime, timedelta, timezone

from psycopg import AsyncConnection

ACTIVE_STATUSES = ["started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing"]
FINAL_STATUSES = ["succeeded", "failed", "stopped"]


async def _insert(conn: AsyncConnection, table: str, values: dict) -> dict:
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    cur = await conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *",  # test helper: names are fixed
        list(values.values()),
    )
    return await cur.fetchone()


async def insert_user(conn: AsyncConnection, **overrides) -> dict:
    values = {"name": "Test User", "email": f"user-{uuid.uuid4().hex[:12]}@example.com", "password_hash": "$2b$12$hash"}
    values.update(overrides)
    return await _insert(conn, "users", values)


async def insert_token(conn: AsyncConnection, user_id: int, **overrides) -> dict:
    values = {"user_id": user_id, "token_type": "email_verification", "token_hash": uuid.uuid4().hex,
              "expires_at": _now_plus(hours=24)}
    values.update(overrides)
    return await _insert(conn, "user_tokens", values)


async def insert_project(conn: AsyncConnection, user_id: int, **overrides) -> dict:
    values = {"user_id": user_id, "name": "Shop", "language": "java", "source_type": "github",
              "source_reference": "https://github.com/owner/repo", "test_command": "mvn -q test"}
    values.update(overrides)
    return await _insert(conn, "projects", values)


async def insert_run(conn: AsyncConnection, user_id: int, project_id: int, **overrides) -> dict:
    values = {"user_id": user_id, "project_id": project_id, "bug_description": "Discount shows 5% not 10%",
              "status": "started", "language": "java", "test_command": "mvn -q test", "source_type": "github",
              "source_reference": "https://github.com/owner/repo", "llm_model": "openai/gpt-oss-120b"}
    values.update(overrides)
    return await _insert(conn, "runs", values)


async def insert_attempt(conn: AsyncConnection, run_id: int, **overrides) -> dict:
    values = {"run_id": run_id, "attempt_number": 1, "reason_started": "first", "explanation": "Fix the rate",
              "status": "waiting_for_approval"}
    values.update(overrides)
    return await _insert(conn, "attempts", values)


async def insert_edit(conn: AsyncConnection, attempt_id: int, **overrides) -> dict:
    values = {"attempt_id": attempt_id, "file_path": "src/Discount.java", "start_line": 3, "end_line": 3,
              "old_code": "rate = 0.05;", "new_code": "rate = 0.10;", "edit_order": 1}
    values.update(overrides)
    return await _insert(conn, "file_edits", values)


async def insert_event(conn: AsyncConnection, run_id: int, **overrides) -> dict:
    values = {"run_id": run_id, "sequence_number": 1, "event_type": "status_changed", "message": "Run started"}
    values.update(overrides)
    return await _insert(conn, "run_events", values)


async def insert_test_result(conn: AsyncConnection, run_id: int, **overrides) -> dict:
    values = {"run_id": run_id, "test_type": "baseline", "status": "failed", "exit_code": 1,
              "log_excerpt": "Tests run: 3, Failures: 1", "duration_ms": 1200, "docker_image": "maven:3-eclipse-temurin-17"}
    values.update(overrides)
    return await _insert(conn, "test_results", values)


async def insert_package(conn: AsyncConnection, run_id: int, **overrides) -> dict:
    values = {"run_id": run_id, "storage_path": "/data/packages/1.zip", "size_bytes": 1024,
              "expires_at": _now_plus(days=7)}
    values.update(overrides)
    return await _insert(conn, "packages", values)


def _now_plus(**delta) -> object:
    return datetime.now(timezone.utc) + timedelta(**delta)


async def user_with_token(conn: AsyncConnection, **overrides) -> dict:
    """A user inserted directly (no bcrypt, no email) plus a valid JWT and the matching headers."""
    from app.core.security import create_access_token
    from tests.conftest import TEST_JWT_SECRET

    user = await insert_user(conn, **overrides)
    token = create_access_token(user["id"], TEST_JWT_SECRET, 24)
    return {**user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}
