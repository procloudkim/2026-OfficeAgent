"""Forward-only PostgreSQL migration runner with checksum enforcement."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg

from officeagent.infrastructure.settings import Settings

MIGRATION_LOCK_KEY = 4_732_838_836_560_323_702
MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_[a-z0-9][a-z0-9_]*\.sql$")


class MigrationError(RuntimeError):
    """Base class for fail-closed migration errors."""


class MigrationDiscoveryError(MigrationError):
    """Raised when checked-in migration files are ambiguous or invalid."""


class MigrationChecksumMismatch(MigrationError):
    """Raised when an applied migration file was changed."""


class MigrationHistoryError(MigrationError):
    """Raised when the database references a missing migration file."""


class MigrationExecutionError(MigrationError):
    """Raised after the current migration transaction is rolled back."""


@dataclass(frozen=True)
class MigrationFile:
    """One immutable migration file and its content digest."""

    version: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationReport:
    """Sanitized migration and runtime version receipt."""

    migration_head: str
    applied_now: tuple[str, ...]
    already_applied: tuple[str, ...]
    server_version: str
    vector_version: str


def default_migrations_path() -> Path:
    """Return the packaged forward-only SQL directory."""

    return Path(__file__).resolve().parent.parent / "migrations"


def discover_migrations(directory: Path | None = None) -> tuple[MigrationFile, ...]:
    """Load ordered SQL files and reject duplicate or malformed versions."""

    migration_dir = directory or default_migrations_path()
    if not migration_dir.is_dir():
        raise MigrationDiscoveryError(f"migration directory not found: {migration_dir}")

    discovered: list[MigrationFile] = []
    versions: set[str] = set()
    for path in sorted(migration_dir.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(f"invalid migration filename: {path.name}")
        version = match.group("version")
        if version in versions:
            raise MigrationDiscoveryError(f"duplicate migration version: {version}")
        versions.add(version)
        data = path.read_bytes()
        discovered.append(
            MigrationFile(
                version=version,
                path=path,
                checksum=hashlib.sha256(data).hexdigest(),
                sql=data.decode("utf-8"),
            )
        )
    if not discovered:
        raise MigrationDiscoveryError(f"no migration files found: {migration_dir}")
    return tuple(discovered)


def _create_ledger(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )


def _read_applied(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[str, str]:
    rows = conn.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {str(version): str(checksum) for version, checksum in rows}


def _validate_history(
    migrations: Sequence[MigrationFile],
    applied: dict[str, str],
) -> None:
    current = {migration.version: migration for migration in migrations}
    expected_versions = tuple(current)
    applied_versions = tuple(applied)
    missing_files = sorted(set(applied) - set(current))
    if missing_files:
        raise MigrationHistoryError(
            "database contains migrations missing from source: " + ", ".join(missing_files)
        )
    if applied_versions != expected_versions[: len(applied_versions)]:
        raise MigrationHistoryError(
            "applied migrations are not a forward-only prefix: "
            f"database={applied_versions} source={expected_versions}"
        )
    for version, checksum in applied.items():
        if current[version].checksum != checksum:
            raise MigrationChecksumMismatch(
                f"migration {version} checksum changed: "
                f"database={checksum} source={current[version].checksum}"
            )


def _build_report(
    conn: psycopg.Connection[tuple[object, ...]],
    migrations: Sequence[MigrationFile],
    applied_now: Sequence[str],
) -> MigrationReport:
    applied = _read_applied(conn)
    _validate_history(migrations, applied)
    expected_versions = tuple(migration.version for migration in migrations)
    if tuple(applied) != expected_versions:
        raise MigrationHistoryError(
            f"migration head mismatch: database={tuple(applied)} source={expected_versions}"
        )
    server_version_row = conn.execute("SHOW server_version").fetchone()
    vector_version_row = conn.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    if server_version_row is None or vector_version_row is None:
        raise MigrationHistoryError("PostgreSQL server or vector extension version is unavailable")
    return MigrationReport(
        migration_head=expected_versions[-1],
        applied_now=tuple(applied_now),
        already_applied=tuple(
            version for version in expected_versions if version not in applied_now
        ),
        server_version=str(server_version_row[0]),
        vector_version=str(vector_version_row[0]),
    )


def apply_migrations(
    database_url: str,
    migrations_path: Path | None = None,
) -> MigrationReport:
    """Apply all pending files under a session advisory lock."""

    migrations = discover_migrations(migrations_path)
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            _create_ledger(conn)
            applied = _read_applied(conn)
            _validate_history(migrations, applied)
            for migration in migrations:
                if migration.version in applied:
                    continue
                try:
                    with conn.transaction():
                        conn.execute(migration.sql, prepare=False)
                        conn.execute(
                            """
                            INSERT INTO schema_migrations (version, checksum)
                            VALUES (%s, %s)
                            """,
                            (migration.version, migration.checksum),
                        )
                except Exception as error:
                    raise MigrationExecutionError(
                        f"migration {migration.version} failed and was rolled back"
                    ) from error
                applied_now.append(migration.version)
            return _build_report(conn, migrations, applied_now)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def inspect_migrations(
    database_url: str,
    migrations_path: Path | None = None,
) -> MigrationReport:
    """Verify migration head and runtime versions without changing the database."""

    migrations = discover_migrations(migrations_path)
    with psycopg.connect(database_url, autocommit=True) as conn:
        return _build_report(conn, migrations, ())


def main(argv: Sequence[str] | None = None) -> int:
    """Apply or inspect migrations and print a credential-free JSON receipt."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without applying")
    args = parser.parse_args(argv)
    settings = Settings()
    report = (
        inspect_migrations(settings.database_url)
        if args.check
        else apply_migrations(settings.database_url)
    )
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return 0


def run() -> None:
    """Console-script entry point."""

    raise SystemExit(main())


if __name__ == "__main__":
    run()
