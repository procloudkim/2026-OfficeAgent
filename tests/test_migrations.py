from __future__ import annotations

import hashlib
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from officeagent.infrastructure.migrations import (
    MIGRATION_LOCK_KEY,
    MigrationChecksumMismatch,
    MigrationExecutionError,
    MigrationHistoryError,
    apply_migrations,
    default_migrations_path,
)
from officeagent.infrastructure.test_runner import temporary_database

ADMIN_URL = os.environ.get("OFFICEAGENT_DATABASE_ADMIN_URL")
pytestmark = pytest.mark.skipif(
    not ADMIN_URL,
    reason="requires OFFICEAGENT_DATABASE_ADMIN_URL from the Compose test service",
)


@pytest.fixture
def empty_database_url() -> str:
    assert ADMIN_URL is not None
    with temporary_database(ADMIN_URL) as database_url:
        yield database_url


def copied_migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(default_migrations_path(), target)
    return target


def test_empty_database_applies_schema_and_runtime_versions(
    empty_database_url: str,
) -> None:
    report = apply_migrations(empty_database_url)

    assert report.migration_head == "0001"
    assert report.applied_now == ("0001",)
    assert report.server_version.startswith("17.10")
    assert report.vector_version == "0.8.6"
    with psycopg.connect(empty_database_url) as conn:
        row = conn.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            JOIN pg_class AS relation ON relation.oid = attribute.attrelid
            WHERE relation.relname = 'document_chunks'
              AND attribute.attname = 'embedding'
            """
        ).fetchone()
        assert row == ("vector(384)",)
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)


def test_migration_rerun_is_idempotent(empty_database_url: str) -> None:
    first = apply_migrations(empty_database_url)
    second = apply_migrations(empty_database_url)

    assert first.applied_now == ("0001",)
    assert second.applied_now == ()
    assert second.already_applied == ("0001",)


def test_applied_migration_checksum_change_fails_closed(
    empty_database_url: str,
    tmp_path: Path,
) -> None:
    migrations = copied_migrations(tmp_path)
    apply_migrations(empty_database_url, migrations)
    migration = migrations / "0001_v2_schema.sql"
    migration.write_text(migration.read_text(encoding="utf-8") + "\n-- drift\n", encoding="utf-8")

    with pytest.raises(MigrationChecksumMismatch):
        apply_migrations(empty_database_url, migrations)


def test_failing_migration_rolls_back_schema_and_ledger(
    empty_database_url: str,
    tmp_path: Path,
) -> None:
    migrations = copied_migrations(tmp_path)
    apply_migrations(empty_database_url, migrations)
    (migrations / "9999_failure_probe.sql").write_text(
        "CREATE TABLE migration_failure_probe (id integer);\nSELECT 1 / 0;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationExecutionError):
        apply_migrations(empty_database_url, migrations)

    with psycopg.connect(empty_database_url) as conn:
        assert conn.execute(
            "SELECT to_regclass('public.migration_failure_probe')"
        ).fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM schema_migrations WHERE version = '9999'"
        ).fetchone() == (0,)


def test_non_prefix_history_fails_before_applying_missing_earlier_file(
    empty_database_url: str,
    tmp_path: Path,
) -> None:
    migrations = copied_migrations(tmp_path)
    later_file = migrations / "0002_later.sql"
    later_file.write_text("CREATE TABLE later_probe (id integer);\n", encoding="utf-8")
    later_checksum = hashlib.sha256(later_file.read_bytes()).hexdigest()
    with psycopg.connect(empty_database_url) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version text PRIMARY KEY,
                checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, checksum) VALUES ('0002', %s)",
            (later_checksum,),
        )
        conn.commit()

    with pytest.raises(MigrationHistoryError, match="not a forward-only prefix"):
        apply_migrations(empty_database_url, migrations)

    with psycopg.connect(empty_database_url) as conn:
        assert conn.execute("SELECT to_regclass('public.documents')").fetchone() == (None,)
        assert conn.execute("SELECT to_regclass('public.later_probe')").fetchone() == (None,)


def test_migration_advisory_lock_serializes_concurrent_runners(
    empty_database_url: str,
) -> None:
    with psycopg.connect(empty_database_url, autocommit=True) as lock_holder:
        lock_holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(apply_migrations, empty_database_url)
            deadline = time.monotonic() + 5
            waiting = False
            while time.monotonic() < deadline:
                row = lock_holder.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks
                        WHERE locktype = 'advisory' AND NOT granted
                    )
                    """
                ).fetchone()
                waiting = bool(row and row[0])
                if waiting:
                    break
                time.sleep(0.05)
            assert waiting
            assert not future.done()
            lock_holder.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            report = future.result(timeout=10)

    assert report.applied_now == ("0001",)
