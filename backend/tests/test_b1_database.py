"""Phase B1: Database (migration 001, the migration command, the connection pool)."""

import ast
import asyncio
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.migrate import MIGRATIONS_DIR, MigrationError, discover, main as migrate_main, run_migrations
from tests.conftest import BACKEND_DIR, OUR_TABLES, TEST_DATABASE_URL, database_url_for, find_secrets
from tests.factories import (
    _insert,
    ACTIVE_STATUSES,
    insert_attempt,
    insert_edit,
    insert_event,
    insert_package,
    insert_project,
    insert_run,
    insert_test_result,
    insert_token,
    insert_user,
)

T, B, I, TS, BOOL, J = "text", "bigint", "integer", "timestamp with time zone", "boolean", "jsonb"

# The columns and types of 03_database_design.md: {table: {column: (type, nullable)}}.
EXPECTED_SCHEMA: dict[str, dict[str, tuple[str, bool]]] = {
    "users": {
        "id": (B, False), "name": (T, False), "email": (T, False), "password_hash": (T, False),
        "is_email_verified": (BOOL, False), "password_changed_at": (TS, False),
        "created_at": (TS, False), "updated_at": (TS, False),
    },
    "user_tokens": {
        "id": (B, False), "user_id": (B, False), "token_type": (T, False), "token_hash": (T, False),
        "expires_at": (TS, False), "used_at": (TS, True), "created_at": (TS, False),
    },
    "projects": {
        "id": (B, False), "user_id": (B, False), "name": (T, False), "language": (T, False),
        "source_type": (T, False), "source_reference": (T, True), "branch": (T, True),
        "test_command": (T, False), "created_at": (TS, False), "updated_at": (TS, False),
    },
    "runs": {
        "id": (B, False), "user_id": (B, False), "project_id": (B, False), "bug_description": (T, False),
        "status": (T, False), "language": (T, False), "test_command": (T, False), "branch": (T, True),
        "source_type": (T, False), "source_reference": (T, True), "source_commit": (T, True),
        "llm_model": (T, False), "workspace_path": (T, True), "workspace_deleted_at": (TS, True),
        "stop_reason": (T, True), "reject_count": (I, False), "failed_test_count": (I, False),
        "hint_count": (I, False), "final_summary": (T, True), "total_tokens": (I, True),
        "applied_to_source_at": (TS, True), "started_at": (TS, False), "finished_at": (TS, True),
        "created_at": (TS, False), "updated_at": (TS, False),
    },
    "attempts": {
        "id": (B, False), "run_id": (B, False), "attempt_number": (I, False), "reason_started": (T, False),
        "explanation": (T, True), "status": (T, False), "decision": (T, True), "decision_feedback": (T, True),
        "decided_at": (TS, True), "applied_at": (TS, True), "reverted_at": (TS, True),
        "created_at": (TS, False), "updated_at": (TS, False),
    },
    "file_edits": {
        "id": (B, False), "attempt_id": (B, False), "file_path": (T, False), "start_line": (I, False),
        "end_line": (I, False), "old_code": (T, False), "new_code": (T, False), "edit_order": (I, False),
        "explanation": (T, True), "created_at": (TS, False),
    },
    "run_events": {
        "id": (B, False), "run_id": (B, False), "attempt_id": (B, True), "sequence_number": (I, False),
        "event_type": (T, False), "message": (T, False), "details": (J, True), "created_at": (TS, False),
    },
    "test_results": {
        "id": (B, False), "run_id": (B, False), "attempt_id": (B, True), "test_type": (T, False),
        "status": (T, False), "exit_code": (I, True), "log_excerpt": (T, True), "duration_ms": (I, True),
        "docker_image": (T, True), "started_at": (TS, True), "finished_at": (TS, True), "created_at": (TS, False),
    },
    "packages": {
        "id": (B, False), "run_id": (B, False), "storage_path": (T, False), "size_bytes": (B, False),
        "expires_at": (TS, False), "deleted_at": (TS, True), "created_at": (TS, False),
    },
}

INJECTION = "'; DROP TABLE users; --"


async def make_tree(conn) -> dict:
    """A user with one of everything under them. Returns the ids."""
    user = await insert_user(conn)
    token = await insert_token(conn, user["id"])
    project = await insert_project(conn, user["id"])
    run = await insert_run(conn, user["id"], project["id"], status="succeeded")
    attempt = await insert_attempt(conn, run["id"], status="tests_passed", decision="accepted")
    edit = await insert_edit(conn, attempt["id"])
    event = await insert_event(conn, run["id"], attempt_id=attempt["id"])
    baseline = await insert_test_result(conn, run["id"])
    after_fix = await insert_test_result(conn, run["id"], test_type="after_fix", attempt_id=attempt["id"], status="passed")
    package = await insert_package(conn, run["id"])
    return {
        "users": user["id"], "user_tokens": token["id"], "projects": project["id"], "runs": run["id"],
        "attempts": attempt["id"], "file_edits": edit["id"], "run_events": event["id"],
        "test_results": [baseline["id"], after_fix["id"]], "packages": package["id"],
    }


async def exists(conn, table: str, ids) -> bool:
    ids = ids if isinstance(ids, list) else [ids]
    cur = await conn.execute(f"SELECT count(*) AS n FROM {table} WHERE id = ANY(%s)", (ids,))
    return (await cur.fetchone())["n"] == len(ids)


async def none_left(conn, table: str, ids) -> bool:
    ids = ids if isinstance(ids, list) else [ids]
    cur = await conn.execute(f"SELECT count(*) AS n FROM {table} WHERE id = ANY(%s)", (ids,))
    return (await cur.fetchone())["n"] == 0


def copy_migrations(target: Path) -> Path:
    target.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(path, target / path.name)
    return target


def applied_versions(url: str) -> list[int]:
    with psycopg.connect(url) as conn:
        return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


# --- Functional ---------------------------------------------------------------------------------------

def test_B1_F01_migrations_on_empty_database_create_the_9_tables(empty_database):
    applied = run_migrations(empty_database)
    assert applied == ["001_initial_schema.sql"]
    with psycopg.connect(empty_database) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'schema_migrations'"
        ).fetchall()
    actual: dict[str, dict[str, tuple[str, bool]]] = {}
    for table, column, data_type, nullable in rows:
        actual.setdefault(table, {})[column] = (data_type, nullable == "YES")
    assert actual == EXPECTED_SCHEMA


def test_B1_F02_running_again_applies_nothing(empty_database):
    run_migrations(empty_database)
    assert run_migrations(empty_database) == []
    assert applied_versions(empty_database) == [1]


def test_B1_F03_only_the_new_file_runs(empty_database, tmp_path):
    folder = copy_migrations(tmp_path / "migrations")
    run_migrations(empty_database, folder)
    (folder / "002_add_test_table.sql").write_text("CREATE TABLE b1_f03_check (id integer);")
    assert run_migrations(empty_database, folder) == ["002_add_test_table.sql"]
    assert applied_versions(empty_database) == [1, 2]


async def test_B1_F04_one_valid_row_in_every_table(db):
    ids = await make_tree(db)
    for table in OUR_TABLES:
        assert await exists(db, table, ids[table]), table


# --- Failure: database rules ----------------------------------------------------------------------

async def test_B1_X01_unique_rules(db):
    user = await insert_user(db, email="same@example.com")
    with pytest.raises(errors.UniqueViolation):
        await insert_user(db, email="same@example.com")

    await insert_token(db, user["id"], token_hash="abc")
    with pytest.raises(errors.UniqueViolation):
        await insert_token(db, user["id"], token_hash="abc")

    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    await insert_event(db, run["id"], sequence_number=1)
    with pytest.raises(errors.UniqueViolation):
        await insert_event(db, run["id"], sequence_number=1)

    attempt = await insert_attempt(db, run["id"], attempt_number=1)
    with pytest.raises(errors.UniqueViolation):
        await insert_attempt(db, run["id"], attempt_number=1)

    await insert_edit(db, attempt["id"], edit_order=1)
    with pytest.raises(errors.UniqueViolation):
        await insert_edit(db, attempt["id"], edit_order=1)

    await insert_package(db, run["id"])
    with pytest.raises(errors.UniqueViolation):
        await insert_package(db, run["id"], storage_path="/data/packages/other.zip")


async def test_B1_X01_email_must_be_stored_lowercase(db):
    with pytest.raises(errors.CheckViolation):
        await insert_user(db, email="Mixed@Example.com")


NOT_NULL_COLUMNS = {
    table: [column for column, (_, nullable) in columns.items() if not nullable and column != "id"]
    for table, columns in EXPECTED_SCHEMA.items()
}


@pytest.mark.parametrize(
    ("table", "column"),
    [(table, column) for table, columns in NOT_NULL_COLUMNS.items() for column in columns],
)
async def test_B1_X02_not_null_rules(db, table, column):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    attempt = await insert_attempt(db, run["id"])
    # One valid row per table; then the column under test is set to NULL.
    valid = {
        "users": {"name": "N", "email": "nn@example.com", "password_hash": "h", "is_email_verified": False},
        "user_tokens": {"user_id": user["id"], "token_type": "password_reset", "token_hash": "t",
                        "expires_at": datetime.now(timezone.utc)},
        "projects": {"user_id": user["id"], "name": "P", "language": "java", "source_type": "zip",
                     "test_command": "mvn test"},
        "runs": {"user_id": user["id"], "project_id": project["id"], "bug_description": "b", "status": "failed",
                 "language": "java", "test_command": "t", "source_type": "zip", "llm_model": "m"},
        "attempts": {"run_id": run["id"], "attempt_number": 2, "reason_started": "first", "status": "proposed"},
        "file_edits": {"attempt_id": attempt["id"], "file_path": "a", "start_line": 1, "end_line": 1,
                       "old_code": "x", "new_code": "y", "edit_order": 1},
        "run_events": {"run_id": run["id"], "sequence_number": 1, "event_type": "error", "message": "m"},
        "test_results": {"run_id": run["id"], "test_type": "baseline", "status": "error"},
        "packages": {"run_id": run["id"], "storage_path": "p", "size_bytes": 1,
                     "expires_at": datetime.now(timezone.utc) + timedelta(days=7)},
    }[table]
    # Columns with a DEFAULT (created_at, counters ...) are also refused when set to NULL explicitly.
    with pytest.raises(errors.NotNullViolation):
        await _insert(db, table, {**valid, column: None})
    await _insert(db, table, valid)  # the same row without the NULL is fine


async def test_B1_X03_check_rules(db):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    attempt = await insert_attempt(db, run["id"])
    bad = [
        lambda: insert_project(db, user["id"], language="go"),
        lambda: insert_project(db, user["id"], source_type="ftp"),
        lambda: insert_run(db, user["id"], project["id"], status="unknown"),
        lambda: insert_run(db, user["id"], project["id"], status="failed", language="go"),
        lambda: insert_run(db, user["id"], project["id"], status="failed", source_type="ftp"),
        lambda: insert_event(db, run["id"], event_type="unknown"),
        lambda: insert_token(db, user["id"], token_type="unknown"),
        lambda: insert_test_result(db, run["id"], status="unknown"),
        lambda: insert_test_result(db, run["id"], test_type="unknown"),
        lambda: insert_attempt(db, run["id"], attempt_number=5, decision="maybe"),
        lambda: insert_attempt(db, run["id"], attempt_number=6, reason_started="unknown"),
        lambda: insert_attempt(db, run["id"], attempt_number=7, status="unknown"),
    ]
    for make in bad:
        with pytest.raises(errors.CheckViolation):
            await make()
    assert attempt["id"]


async def test_B1_X04_number_rules(db):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    attempt = await insert_attempt(db, run["id"])
    bad = [
        lambda: insert_run(db, user["id"], project["id"], status="failed", reject_count=-1),
        lambda: insert_run(db, user["id"], project["id"], status="failed", failed_test_count=-1),
        lambda: insert_run(db, user["id"], project["id"], status="failed", hint_count=-1),
        lambda: insert_attempt(db, run["id"], attempt_number=0),
        lambda: insert_edit(db, attempt["id"], start_line=0, end_line=1),
        lambda: insert_edit(db, attempt["id"], start_line=5, end_line=4),
        lambda: insert_test_result(db, run["id"], duration_ms=-1),
        lambda: insert_package(db, run["id"], size_bytes=-1),
        lambda: insert_event(db, run["id"], sequence_number=0),
    ]
    for make in bad:
        with pytest.raises(errors.CheckViolation):
            await make()
    # The edges are allowed.
    await insert_edit(db, attempt["id"], start_line=1, end_line=1, edit_order=2)


async def test_B1_X05_project_reference_rules(db):
    user = await insert_user(db)
    for source_type, reference in [("github", None), ("github", ""), ("local_path", None), ("local_path", "")]:
        with pytest.raises(errors.CheckViolation):
            await insert_project(db, user["id"], source_type=source_type, source_reference=reference)
    # zip without a reference is fine; the design only requires one for non-zip projects.
    await insert_project(db, user["id"], source_type="zip", source_reference=None)
    await insert_project(db, user["id"], source_type="zip", source_reference="upload.zip")


async def test_B1_X06_test_result_rules(db):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    attempt = await insert_attempt(db, run["id"])
    with pytest.raises(errors.CheckViolation):
        await insert_test_result(db, run["id"], test_type="baseline", attempt_id=attempt["id"])
    with pytest.raises(errors.CheckViolation):
        await insert_test_result(db, run["id"], test_type="after_fix", attempt_id=None)
    await insert_test_result(db, run["id"])
    with pytest.raises(errors.UniqueViolation):
        await insert_test_result(db, run["id"])
    await insert_test_result(db, run["id"], test_type="after_fix", attempt_id=attempt["id"])
    with pytest.raises(errors.UniqueViolation):
        await insert_test_result(db, run["id"], test_type="after_fix", attempt_id=attempt["id"])


async def test_B1_X07_package_expires_before_created(db):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    with pytest.raises(errors.CheckViolation):
        await insert_package(db, run["id"], expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))


async def test_B1_X08_foreign_keys(db):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed")
    with pytest.raises(errors.ForeignKeyViolation):
        await insert_run(db, user["id"], 999_999, status="failed")
    with pytest.raises(errors.ForeignKeyViolation):
        await insert_attempt(db, 999_999)
    with pytest.raises(errors.ForeignKeyViolation):
        await insert_event(db, run["id"], attempt_id=999_999)
    with pytest.raises(errors.ForeignKeyViolation):
        await insert_project(db, 999_999)


async def test_B1_X09_cascade_delete_user(db):
    a, b = await make_tree(db), await make_tree(db)
    await db.execute("DELETE FROM users WHERE id = %s", (a["users"],))
    for table in OUR_TABLES:
        assert await none_left(db, table, a[table]), table
        assert await exists(db, table, b[table]), table


async def test_B1_X09_cascade_delete_project(db):
    tree = await make_tree(db)
    other_project = await insert_project(db, tree["users"])
    other_run = await insert_run(db, tree["users"], other_project["id"], status="failed")
    await db.execute("DELETE FROM projects WHERE id = %s", (tree["projects"],))
    for table in ["projects", "runs", "attempts", "file_edits", "run_events", "test_results", "packages"]:
        assert await none_left(db, table, tree[table]), table
    assert await exists(db, "users", tree["users"])
    assert await exists(db, "user_tokens", tree["user_tokens"])
    assert await exists(db, "projects", other_project["id"])
    assert await exists(db, "runs", other_run["id"])


async def test_B1_X09_cascade_delete_run(db):
    tree = await make_tree(db)
    other_run = await insert_run(db, tree["users"], tree["projects"], status="failed")
    other_event = await insert_event(db, other_run["id"])
    await db.execute("DELETE FROM runs WHERE id = %s", (tree["runs"],))
    for table in ["runs", "attempts", "file_edits", "run_events", "test_results", "packages"]:
        assert await none_left(db, table, tree[table]), table
    for table in ["users", "user_tokens", "projects"]:
        assert await exists(db, table, tree[table]), table
    assert await exists(db, "runs", other_run["id"])
    assert await exists(db, "run_events", other_event["id"])


# --- Failure: the migration command ---------------------------------------------------------------

def test_B1_X10_error_in_the_middle_rolls_back_the_whole_file(empty_database, tmp_path):
    folder = copy_migrations(tmp_path / "migrations")
    run_migrations(empty_database, folder)
    (folder / "002_broken.sql").write_text(
        "CREATE TABLE b1_x10_half (id integer);\n"
        "ALTER TABLE users ADD COLUMN b1_x10 text;\n"
        "THIS IS NOT SQL;\n"
        "CREATE TABLE b1_x10_never (id integer);\n"
    )
    with pytest.raises(MigrationError) as error:
        run_migrations(empty_database, folder)
    assert "002_broken.sql" in str(error.value)
    with psycopg.connect(empty_database) as conn:
        tables = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        columns = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")}
    assert "b1_x10_half" not in tables and "b1_x10_never" not in tables
    assert "b1_x10" not in columns
    assert applied_versions(empty_database) == [1]


def test_B1_X11_changed_applied_file_is_refused(empty_database, tmp_path):
    folder = copy_migrations(tmp_path / "migrations")
    run_migrations(empty_database, folder)
    path = folder / "001_initial_schema.sql"
    path.write_text(path.read_text() + "\n-- a small edit\n")
    with pytest.raises(MigrationError) as error:
        run_migrations(empty_database, folder)
    assert "001_initial_schema.sql" in str(error.value) and "changed" in str(error.value)


def test_B1_X11_applied_file_removed_is_refused(empty_database, tmp_path):
    folder = copy_migrations(tmp_path / "migrations")
    (folder / "002_extra.sql").write_text("CREATE TABLE b1_x11 (id integer);")
    run_migrations(empty_database, folder)
    (folder / "002_extra.sql").unlink()
    with pytest.raises(MigrationError, match="missing"):
        run_migrations(empty_database, folder)


def test_B1_X12_same_number_or_gap_is_refused(tmp_path):
    same = copy_migrations(tmp_path / "same")
    (same / "001_other.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="001"):
        discover(same)

    gap = copy_migrations(tmp_path / "gap")
    (gap / "003_skipped_two.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="002"):
        discover(gap)

    bad_name = copy_migrations(tmp_path / "bad_name")
    (bad_name / "2_no_padding.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="2_no_padding.sql"):
        discover(bad_name)


def test_B1_X12_command_refuses_with_exit_code(tmp_path, monkeypatch, capsys):
    gap = copy_migrations(tmp_path / "gap")
    (gap / "003_skipped_two.sql").write_text("SELECT 1;")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    assert migrate_main(["--dir", str(gap)]) == 1
    assert "Gap" in capsys.readouterr().err


def test_B1_X13_database_not_reachable(tmp_path):
    with pytest.raises(MigrationError, match="Cannot connect"):
        run_migrations("postgresql://sangra:nopass@127.0.0.1:1/nothing")
    env = {"DATABASE_URL": "postgresql://sangra:nopass@127.0.0.1:1/nothing", "PATH": "/usr/bin:/bin",
           "PYTHONPATH": str(BACKEND_DIR)}
    proc = subprocess.run([sys.executable, "-m", "app.migrate"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "Cannot connect to the database" in proc.stderr
    assert "nopass" not in proc.stderr and "Traceback" not in proc.stderr


# --- Security -------------------------------------------------------------------------------------

def _sql_built_from_strings(tree: ast.AST) -> list[int]:
    """Lines where execute()/executemany() gets an f-string, a + join, % formatting or .format()."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("execute", "executemany") and node.args:
            sql = node.args[0]
            built = isinstance(sql, (ast.JoinedStr, ast.BinOp)) or (
                isinstance(sql, ast.Call) and isinstance(sql.func, ast.Attribute) and sql.func.attr in ("format", "join"))
            if built:
                bad.append(node.lineno)
    return bad


def test_B1_S01_no_sql_built_from_strings_in_app_code():
    offenders = {}
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        lines = _sql_built_from_strings(ast.parse(path.read_text(), filename=str(path)))
        if lines:
            offenders[str(path.relative_to(BACKEND_DIR))] = lines
    assert offenders == {}
    # The scanner itself finds the bad patterns.
    assert _sql_built_from_strings(ast.parse('conn.execute(f"SELECT {x}")\nconn.execute("A" + b)')) == [1, 2]


async def test_B1_S02_injection_text_is_stored_as_plain_text(db):
    # Every text column gets the payload through parameters. (Each repository phase repeats this
    # through its own functions: B3-S04 for auth, and so on.)
    user = await insert_user(db, name=INJECTION, password_hash=INJECTION)
    await insert_token(db, user["id"], token_hash=INJECTION)
    project = await insert_project(db, user["id"], name=INJECTION, source_reference=INJECTION,
                                   branch=INJECTION, test_command=INJECTION)
    run = await insert_run(db, user["id"], project["id"], status="failed", bug_description=INJECTION,
                           test_command=INJECTION, branch=INJECTION, source_reference=INJECTION,
                           source_commit=INJECTION, llm_model=INJECTION, workspace_path=INJECTION,
                           stop_reason=INJECTION, final_summary=INJECTION)
    attempt = await insert_attempt(db, run["id"], explanation=INJECTION, decision_feedback=INJECTION)
    await insert_edit(db, attempt["id"], file_path=INJECTION, old_code=INJECTION, new_code=INJECTION,
                      explanation=INJECTION)
    await insert_event(db, run["id"], message=INJECTION, details=Jsonb({"note": INJECTION}))
    await insert_test_result(db, run["id"], log_excerpt=INJECTION, docker_image=INJECTION)
    await insert_package(db, run["id"], storage_path=INJECTION)

    cur = await db.execute("SELECT name, password_hash FROM users WHERE id = %s", (user["id"],))
    assert await cur.fetchone() == {"name": INJECTION, "password_hash": INJECTION}
    for table in OUR_TABLES:
        cur = await db.execute(f"SELECT count(*) AS n FROM {table}")
        assert (await cur.fetchone())["n"] >= 1, table


# --- Concurrency ----------------------------------------------------------------------------------

async def _connections(url: str, count: int) -> list[psycopg.AsyncConnection]:
    return await asyncio.gather(*(
        psycopg.AsyncConnection.connect(url, autocommit=True, row_factory=dict_row) for _ in range(count)))


async def _insert_active_runs(conns, user_ids, project_ids) -> list[object]:
    async def one(conn, user_id, project_id):
        try:
            return await insert_run(conn, user_id, project_id, status="started")
        except errors.UniqueViolation as exc:
            return exc

    return await asyncio.gather(*(one(c, u, p) for c, u, p in zip(conns, user_ids, project_ids)))


async def test_B1_C01_C02_one_active_run_per_user(db, migrated_database):
    user = await insert_user(db)
    project = await insert_project(db, user["id"])
    conns = await _connections(migrated_database, 50)
    try:
        results = await _insert_active_runs(conns, [user["id"]] * 50, [project["id"]] * 50)
    finally:
        await asyncio.gather(*(c.close() for c in conns))
    created = [r for r in results if isinstance(r, dict)]
    rejected = [r for r in results if isinstance(r, errors.UniqueViolation)]
    assert len(created) == 1 and len(rejected) == 49

    # B1-C02: once that run ends, a new active run is allowed.
    await db.execute("UPDATE runs SET status = 'succeeded' WHERE id = %s", (created[0]["id"],))
    await insert_run(db, user["id"], project["id"], status="started")
    # Every active status counts as active.
    for status in ACTIVE_STATUSES:
        with pytest.raises(errors.UniqueViolation):
            await insert_run(db, user["id"], project["id"], status=status)


async def test_B1_C03_active_runs_for_50_different_users(db, migrated_database):
    users = [await insert_user(db) for _ in range(50)]
    projects = [await insert_project(db, u["id"]) for u in users]
    conns = await _connections(migrated_database, 50)
    try:
        results = await _insert_active_runs(conns, [u["id"] for u in users], [p["id"] for p in projects])
    finally:
        await asyncio.gather(*(c.close() for c in conns))
    assert all(isinstance(r, dict) for r in results)


async def test_B1_C04_100_tasks_on_a_pool_of_10(make_settings, migrated_database):
    from app.core.db import close_pool, create_pool, open_pool

    pool = create_pool(make_settings({"DB_POOL_MAX_SIZE": "10", "DB_POOL_TIMEOUT_SECONDS": "30"}))
    await open_pool(pool)
    try:
        async def task(n: int) -> int:
            async with pool.connection() as conn:
                await conn.execute("SELECT pg_sleep(0.02)")
                cur = await conn.execute("SELECT %s::int AS n", (n,))
                return (await cur.fetchone())["n"]

        results = await asyncio.wait_for(asyncio.gather(*(task(n) for n in range(100))), timeout=30)
        assert sorted(results) == list(range(100))
        assert pool.get_stats()["pool_size"] <= 10
    finally:
        await close_pool(pool)


# --- Recovery -------------------------------------------------------------------------------------

async def test_B1_R01_connection_killed_in_the_middle_of_a_transaction(db, make_settings):
    from app.core.db import close_pool, create_pool, open_pool

    pool = create_pool(make_settings({"DB_POOL_MIN_SIZE": "2", "DB_POOL_MAX_SIZE": "2"}))
    await open_pool(pool)
    try:
        with pytest.raises(psycopg.OperationalError):
            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO users (name, email, password_hash) VALUES ('Half', 'half@example.com', 'x')")
                    cur = await conn.execute("SELECT pg_backend_pid() AS pid")
                    pid = (await cur.fetchone())["pid"]
                    await db.execute("SELECT pg_terminate_backend(%s)", (pid,))
                    await conn.execute("SELECT 1")
        cur = await db.execute("SELECT count(*) AS n FROM users WHERE email = 'half@example.com'")
        assert (await cur.fetchone())["n"] == 0
        for _ in range(3):  # the pool hands out working connections afterwards
            async with pool.connection() as conn:
                cur = await conn.execute("SELECT 1 AS ok")
                assert (await cur.fetchone())["ok"] == 1
    finally:
        await close_pool(pool)


# --- Load (a separate database filled with 10,000 runs and 100,000 events) --------------------------

@pytest.fixture(scope="module")
def big_database():
    import uuid

    name = f"sangra_test_big_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = database_url_for(name)
    try:
        run_migrations(url)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("""
                INSERT INTO users (name, email, password_hash)
                SELECT 'User ' || g, 'user' || g || '@example.com', 'x' FROM generate_series(1, 100) g""")
            conn.execute("""
                INSERT INTO projects (user_id, name, language, source_type, source_reference, test_command)
                SELECT id, 'P', 'java', 'github', 'https://github.com/o/r', 'mvn test' FROM users""")
            # 10,000 runs (100 per user), spread over 100 days; every 50th run waits for approval
            # (one per user at most, older than 1 hour) so the "stuck" query has something to find.
            conn.execute("""
                INSERT INTO runs (user_id, project_id, bug_description, status, language, test_command,
                                  source_type, source_reference, llm_model, workspace_path,
                                  created_at, updated_at, started_at)
                SELECT p.user_id, p.id, 'bug ' || g,
                       CASE WHEN g = 1 THEN 'waiting_for_approval' ELSE 'succeeded' END,
                       'java', 'mvn test', 'github', 'https://github.com/o/r', 'm', '/ws/' || p.user_id || '/' || g,
                       now() - (g || ' hours')::interval, now() - (g || ' hours')::interval,
                       now() - (g || ' hours')::interval
                FROM projects p CROSS JOIN generate_series(1, 100) g""")
            conn.execute("""
                INSERT INTO run_events (run_id, sequence_number, event_type, message)
                SELECT r.id, g, 'tool_called', 'event ' || g FROM runs r CROSS JOIN generate_series(1, 10) g""")
            conn.execute("""
                INSERT INTO packages (run_id, storage_path, size_bytes, created_at, expires_at)
                SELECT id, '/p/' || id || '.zip', 1000, created_at, created_at + interval '1 hour' FROM runs""")
            conn.execute("ANALYZE")
        yield url
    finally:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _timed(conn, sql: str, params: tuple) -> tuple[float, list]:
    conn.execute(sql, params).fetchall()  # warm up
    times = []
    for _ in range(5):
        started = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times), rows


@pytest.mark.slow
def test_B1_L01_list_runs_of_one_user(big_database):
    sql = "SELECT * FROM runs WHERE user_id = %s ORDER BY created_at DESC LIMIT 20 OFFSET %s"
    with psycopg.connect(big_database, row_factory=dict_row) as conn:
        total = conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"]
        events = conn.execute("SELECT count(*) AS n FROM run_events").fetchone()["n"]
        assert (total, events) == (10_000, 100_000)
        ms, rows = _timed(conn, sql, (42, 0))
        ms_page2, _ = _timed(conn, sql, (42, 20))
        count_ms, _ = _timed(conn, "SELECT count(*) FROM runs WHERE user_id = %s", (42,))
        plan = "\n".join(r["QUERY PLAN"] for r in conn.execute("EXPLAIN " + sql, (42, 0)))
    print(f"\nB1-L01: page 1 {ms:.2f} ms, page 2 {ms_page2:.2f} ms, count {count_ms:.2f} ms")
    assert len(rows) == 20
    assert all(r["user_id"] == 42 for r in rows)
    created = [r["created_at"] for r in rows]
    assert created == sorted(created, reverse=True)  # newest first
    assert "runs_user_created_idx" in plan
    assert ms < 50 and ms_page2 < 50 and count_ms < 50


@pytest.mark.slow
def test_B1_L02_events_of_one_run_after_n(big_database):
    with psycopg.connect(big_database, row_factory=dict_row) as conn:
        run_id = conn.execute("SELECT id FROM runs ORDER BY id LIMIT 1 OFFSET 5000").fetchone()["id"]
        sql = "SELECT * FROM run_events WHERE run_id = %s AND sequence_number > %s ORDER BY sequence_number LIMIT 500"
        ms, rows = _timed(conn, sql, (run_id, 4))
    print(f"\nB1-L02: events after N {ms:.2f} ms")
    assert [r["sequence_number"] for r in rows] == list(range(5, 11))
    assert ms < 20


@pytest.mark.slow
def test_B1_L03_background_job_queries(big_database):
    queries = {
        "stuck waiting runs": (
            "SELECT id FROM runs WHERE status IN ('waiting_for_approval', 'waiting_for_hint') "
            "AND updated_at < now() - interval '1 hour'", (), 100),
        "old workspaces": (
            "SELECT id, workspace_path FROM runs WHERE workspace_path IS NOT NULL "
            "AND workspace_deleted_at IS NULL AND created_at < now() - interval '24 hours'", (), None),
        "expired packages": (
            "SELECT id, storage_path FROM packages WHERE deleted_at IS NULL AND expires_at < now() "
            "ORDER BY expires_at LIMIT %s", (1000,), 1000),
    }
    with psycopg.connect(big_database) as conn:
        for name, (sql, params, expected_rows) in queries.items():
            ms, rows = _timed(conn, sql, params)
            print(f"\nB1-L03: {name}: {len(rows)} rows in {ms:.2f} ms")
            assert len(rows) == expected_rows if expected_rows else len(rows) >= 7_600, name
            assert ms < 100, name


@pytest.mark.slow
def test_B1_L04_later_migration_on_a_full_database(big_database, tmp_path):
    folder = copy_migrations(tmp_path / "migrations")
    (folder / "002_later_change.sql").write_text(
        "ALTER TABLE runs ADD COLUMN b1_l04_note text;\n"
        "CREATE INDEX runs_b1_l04_idx ON runs (project_id);\n"
    )
    started = time.perf_counter()
    assert run_migrations(big_database, folder) == ["002_later_change.sql"]
    seconds = time.perf_counter() - started
    print(f"\nB1-L04: migration 002 on 10,000 runs / 100,000 events took {seconds * 1000:.0f} ms")
    assert seconds < 10


def test_B1_secrets_not_in_migration_errors(tmp_path):
    # A wrong password must not be echoed by the migration command.
    with pytest.raises(MigrationError) as error:
        run_migrations(TEST_DATABASE_URL.replace("@", "x@", 1))
    assert not find_secrets(str(error.value))
