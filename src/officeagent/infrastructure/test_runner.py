"""Create an isolated PostgreSQL database, migrate it, run pytest, and remove it."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from officeagent.infrastructure.migrations import apply_migrations

TEST_DATABASE_NAME = re.compile(r"^officeagent_test_[0-9a-f]{12}$")
TEST_CONTAINER_SENTINEL = "OFFICEAGENT_TEST_CONTAINER_ISOLATION"
MAX_DATABASE_CONNINFO_LENGTH = 4_096
_FORBIDDEN_ENVIRONMENT_NAMES = frozenset(
    {
        "API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_ORG_ID",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "OFFICEAGENT_CODEX_API_KEY",
        "TOKEN",
    }
)
_FORBIDDEN_ENVIRONMENT_PREFIXES = ("CODEX_", "OPENAI_")
_FORBIDDEN_ENVIRONMENT_SUFFIXES = ("_API_KEY", "_TOKEN")


def find_forbidden_environment_names(names: Iterable[str]) -> tuple[str, ...]:
    """Return credential-like environment names forbidden in the test container."""

    return tuple(
        sorted(
            name
            for name in names
            if name in _FORBIDDEN_ENVIRONMENT_NAMES
            or name.startswith(_FORBIDDEN_ENVIRONMENT_PREFIXES)
            or name.endswith(_FORBIDDEN_ENVIRONMENT_SUFFIXES)
        )
    )


def validate_test_container_environment(environment: Mapping[str, str]) -> None:
    """Fail closed unless the runner has an isolated, credential-free environment."""

    if environment.get(TEST_CONTAINER_SENTINEL) != "1":
        raise RuntimeError("test container isolation sentinel is required")
    forbidden_names = find_forbidden_environment_names(environment)
    if forbidden_names:
        joined_names = ", ".join(forbidden_names)
        raise RuntimeError(
            "test container includes forbidden credential environment names: "
            f"{joined_names}"
        )


def validate_isolated_database_url(database_url: str) -> None:
    """Fail closed unless conninfo resolves to a disposable test database."""

    if len(database_url) > MAX_DATABASE_CONNINFO_LENGTH:
        raise RuntimeError("test runner requires an isolated test database URL")
    try:
        database_name = conninfo_to_dict(database_url).get("dbname", "")
    except psycopg.ProgrammingError:
        raise RuntimeError("test runner requires an isolated test database URL") from None
    if TEST_DATABASE_NAME.fullmatch(database_name) is None:
        raise RuntimeError("test runner requires an isolated test database URL")


def validate_test_database_environment(environment: Mapping[str, str]) -> None:
    """Guard every pytest session before database-backed fixtures can collect."""

    database_url = environment.get("OFFICEAGENT_DATABASE_URL")
    if database_url is not None:
        validate_isolated_database_url(database_url)


def replace_database_name(database_url: str, database_name: str) -> str:
    """Override the effective database target in PostgreSQL conninfo."""

    if TEST_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError(f"unsafe test database name: {database_name}")
    if len(database_url) > MAX_DATABASE_CONNINFO_LENGTH:
        raise ValueError("invalid database connection string")
    try:
        return make_conninfo(database_url, dbname=database_name)
    except psycopg.ProgrammingError:
        raise ValueError("invalid database connection string") from None


def wait_for_postgres(admin_url: str, timeout_seconds: float = 60.0) -> None:
    """Bound readiness retries before creating the isolated test database."""

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(admin_url, connect_timeout=3):
                return
        except psycopg.OperationalError as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("PostgreSQL did not become ready before the test timeout") from last_error


def create_database(admin_url: str, database_name: str) -> str:
    """Create one validated, randomly named test database."""

    database_url = replace_database_name(admin_url, database_name)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    return database_url


def drop_database(admin_url: str, database_name: str) -> None:
    """Drop only a validated OfficeAgent test database."""

    if TEST_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError(f"unsafe test database name: {database_name}")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (database_name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


@contextmanager
def temporary_database(admin_url: str) -> Iterator[str]:
    """Yield an isolated database URL and always remove the database afterward."""

    database_name = f"officeagent_test_{uuid4().hex[:12]}"
    database_url = create_database(admin_url, database_name)
    try:
        yield database_url
    finally:
        drop_database(admin_url, database_name)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the subscription-free suite against a fresh migrated database."""

    validate_test_container_environment(os.environ)
    admin_url = os.environ.get("OFFICEAGENT_DATABASE_ADMIN_URL")
    if not admin_url:
        raise RuntimeError("OFFICEAGENT_DATABASE_ADMIN_URL is required")
    wait_for_postgres(admin_url)
    with temporary_database(admin_url) as database_url:
        validate_isolated_database_url(database_url)
        os.environ["OFFICEAGENT_DATABASE_URL"] = database_url
        apply_migrations(database_url)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *(argv or ())],
            check=False,
        )
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
