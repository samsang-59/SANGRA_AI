"""The migration command (run MANUALLY; the app never changes tables on startup).

    python -m app.migrate              # applies every new numbered .sql file in backend/migrations
    python -m app.migrate --status     # lists applied and pending files

Rules:
- Files are named NNN_name.sql and numbered 001, 002, ... with no gaps and no repeats.
- Each file runs in its own transaction: an error rolls back the whole file and it is not recorded.
- The checksum of every applied file is stored; an applied file that changed (or disappeared) is refused.
- An advisory lock stops two migration commands from running at the same moment.
"""

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
FILE_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
LOCK_ID = 727274  # any fixed number; only used for pg_advisory_lock

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer     PRIMARY KEY,
    filename   text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class MigrationFile:
    version: int
    path: Path
    checksum: str

    @property
    def filename(self) -> str:
        return self.path.name


class _MigrationSettings(BaseSettings):
    """Only the database URL is needed to migrate (the other settings are not checked here)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    database_url: SecretStr


def discover(migrations_dir: Path) -> list[MigrationFile]:
    """Reads the numbered files. Refuses bad names, repeated numbers and gaps."""
    if not migrations_dir.is_dir():
        raise MigrationError(f"Migrations folder not found: {migrations_dir}")
    files: dict[int, Path] = {}
    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        match = FILE_PATTERN.match(path.name)
        if not match:
            raise MigrationError(f"Bad migration file name {path.name!r}: expected NNN_name.sql (for example 002_add_index.sql)")
        version = int(match.group(1))
        if version in files:
            raise MigrationError(f"Two migration files have the number {version:03d}: {files[version].name} and {path.name}")
        files[version] = path
    versions = sorted(files)
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        missing = sorted(set(range(1, (versions[-1] if versions else 0) + 1)) - set(versions))
        raise MigrationError(f"Gap in migration numbers: missing {', '.join(f'{v:03d}' for v in missing)}")
    return [
        MigrationFile(version, files[version], hashlib.sha256(files[version].read_bytes()).hexdigest())
        for version in versions
    ]


def _applied(conn: psycopg.Connection) -> dict[int, tuple[str, str]]:
    rows = conn.execute("SELECT version, filename, checksum FROM schema_migrations ORDER BY version").fetchall()
    return {version: (filename, checksum) for version, filename, checksum in rows}


def _check_applied_unchanged(files: list[MigrationFile], applied: dict[int, tuple[str, str]]) -> None:
    on_disk = {f.version: f for f in files}
    for version, (filename, checksum) in applied.items():
        current = on_disk.get(version)
        if current is None:
            raise MigrationError(f"Applied migration {filename} is missing from the migrations folder")
        if current.checksum != checksum or current.filename != filename:
            raise MigrationError(
                f"Migration {filename} changed after it was applied (checksum differs). "
                "Never edit an applied migration: add a new numbered file instead."
            )


def run_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies the pending files in order. Returns the names of the files applied now."""
    files = discover(migrations_dir)
    try:
        conn = psycopg.connect(database_url, autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as exc:
        raise MigrationError(f"Cannot connect to the database: {_first_line(exc)}") from None

    applied_now: list[str] = []
    with conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(CREATE_TRACKING_TABLE)
            applied = _applied(conn)
            _check_applied_unchanged(files, applied)
            for migration in files:
                if migration.version in applied:
                    continue
                sql = migration.path.read_text(encoding="utf-8")
                try:
                    with conn.transaction():
                        conn.execute(sql)  # type: ignore[arg-type]  # a whole file, no parameters
                        conn.execute(
                            "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                            (migration.version, migration.filename, migration.checksum),
                        )
                except psycopg.Error as exc:
                    raise MigrationError(
                        f"Migration {migration.filename} failed and was rolled back: {_first_line(exc)}"
                    ) from None
                applied_now.append(migration.filename)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now


def migration_status(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> tuple[list[str], list[str]]:
    files = discover(migrations_dir)
    try:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=10) as conn:
            conn.execute(CREATE_TRACKING_TABLE)
            applied = _applied(conn)
    except psycopg.OperationalError as exc:
        raise MigrationError(f"Cannot connect to the database: {_first_line(exc)}") from None
    _check_applied_unchanged(files, applied)
    return ([f.filename for f in files if f.version in applied], [f.filename for f in files if f.version not in applied])


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrate", description="Apply the numbered .sql migrations.")
    parser.add_argument("--status", action="store_true", help="list applied and pending files, change nothing")
    parser.add_argument("--dir", type=Path, default=MIGRATIONS_DIR, help="migrations folder")
    args = parser.parse_args(argv)
    try:
        database_url = _MigrationSettings().database_url.get_secret_value()  # type: ignore[call-arg]
    except Exception:
        print("Migration failed: DATABASE_URL is missing (set it in .env or the environment)", file=sys.stderr)
        return 1
    try:
        if args.status:
            applied, pending = migration_status(database_url, args.dir)
            print("Applied: " + (", ".join(applied) or "none"))
            print("Pending: " + (", ".join(pending) or "none"))
            return 0
        applied_now = run_migrations(database_url, args.dir)
    except MigrationError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    print("Applied: " + ", ".join(applied_now) if applied_now else "Nothing to apply, the database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
