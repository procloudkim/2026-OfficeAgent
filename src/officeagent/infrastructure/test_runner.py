"""Create an isolated PostgreSQL database, migrate it, run pytest, and remove it."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

from officeagent.infrastructure.migrations import apply_migrations

TEST_DATABASE_NAME = re.compile(r"^officeagent_test_[0-9a-f]{12}$")


def replace_database_name(database_url: str, database_name: str) -> str:
    """Replace only the database path in a PostgreSQL URL."""

    if TEST_DATABASE_NAME.fullmatch(database_name) is None:
        raise ValueError(f"unsafe test database name: {database_name}")
    parsed = urlsplit(database_url)
    return urlunsplit(parsed._replace(path=f"/{database_name}"))


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

    admin_url = os.environ.get("OFFICEAGENT_DATABASE_ADMIN_URL")
    if not admin_url:
        raise RuntimeError("OFFICEAGENT_DATABASE_ADMIN_URL is required")
    wait_for_postgres(admin_url)
    with temporary_database(admin_url) as database_url:
        os.environ["OFFICEAGENT_DATABASE_URL"] = database_url
        apply_migrations(database_url)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *(argv or ())],
            check=False,
        )
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
