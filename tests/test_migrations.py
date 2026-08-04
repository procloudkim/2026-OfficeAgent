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
    inspect_migrations,
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

    assert report.migration_head == "0003"
    assert report.applied_now == ("0001", "0002", "0003")
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
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (3,)
        assert conn.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'documents'
              AND column_name = 'chunking_version'
            """
        ).fetchone() == (None,)
        assert conn.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'documents'
              AND column_name = 'indexing_version'
            """
        ).fetchone() == (None,)


def test_migration_rerun_is_idempotent(empty_database_url: str) -> None:
    first = apply_migrations(empty_database_url)
    second = apply_migrations(empty_database_url)

    assert first.applied_now == ("0001", "0002", "0003")
    assert second.applied_now == ()
    assert second.already_applied == ("0001", "0002", "0003")


def test_chunking_version_migration_preserves_legacy_rows_and_dependencies(
    empty_database_url: str,
    tmp_path: Path,
) -> None:
    migrations = copied_migrations(tmp_path)
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    shutil.copy2(migrations / "0001_v2_schema.sql", legacy_migrations)
    apply_migrations(empty_database_url, legacy_migrations)
    zero_vector = "[" + ",".join("0" for _ in range(384)) + "]"
    document_id = "00000000-0000-4000-8000-000000000001"
    cache_id = "00000000-0000-4000-8000-000000000002"
    with psycopg.connect(empty_database_url) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                document_id, version, normalized_key, filename,
                mime_type, content_hash, ready
            ) VALUES (%s::uuid, 1, 'legacy.txt', 'legacy.txt', 'text/plain', %s, true)
            """,
            (document_id, "0" * 64),
        )
        conn.execute(
            """
            INSERT INTO document_chunks (
                document_id, document_version, chunk_index, content, embedding
            ) VALUES (%s::uuid, 1, 0, 'legacy', %s::vector)
            """,
            (document_id, zero_vector),
        )
        conn.execute(
            """
            INSERT INTO answer_cache (
                cache_id, normalized_question, question_embedding, answer,
                prompt_version, embedding_version, retrieval_version, created_revision
            ) VALUES (%s::uuid, 'legacy?', %s::vector, 'legacy', 'p1', 'e1', 'r1', 0)
            """,
            (cache_id, zero_vector),
        )
        conn.execute(
            """
            INSERT INTO cache_dependencies (cache_id, document_id, document_version)
            VALUES (%s::uuid, %s::uuid, 1)
            """,
            (cache_id, document_id),
        )

    report = apply_migrations(empty_database_url, migrations)

    assert report.applied_now == ("0002", "0003")
    with psycopg.connect(empty_database_url) as conn:
        assert conn.execute(
            "SELECT chunking_version FROM documents WHERE document_id = %s::uuid",
            (document_id,),
        ).fetchone() == ("legacy-page-safe-character-v1:unknown-settings",)
        assert conn.execute(
            "SELECT indexing_version FROM documents WHERE document_id = %s::uuid",
            (document_id,),
        ).fetchone() == ("legacy-document-index-v1:unknown-settings",)
        assert conn.execute("SELECT count(*) FROM document_chunks").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM cache_dependencies").fetchone() == (1,)


def test_indexing_version_migration_preserves_0002_rows(
    empty_database_url: str,
    tmp_path: Path,
) -> None:
    migrations = copied_migrations(tmp_path)
    pre_indexing_migrations = tmp_path / "pre-indexing-migrations"
    pre_indexing_migrations.mkdir()
    shutil.copy2(migrations / "0001_v2_schema.sql", pre_indexing_migrations)
    shutil.copy2(
        migrations / "0002_document_chunking_version.sql",
        pre_indexing_migrations,
    )
    apply_migrations(empty_database_url, pre_indexing_migrations)
    document_id = "00000000-0000-4000-8000-000000000003"
    with psycopg.connect(empty_database_url) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                document_id, version, normalized_key, filename, mime_type,
                content_hash, chunking_version, ready
            ) VALUES (
                %s::uuid, 1, 'indexed.txt', 'indexed.txt', 'text/plain',
                %s, 'page-safe-dual-bound-v1:known', true
            )
            """,
            (document_id, "1" * 64),
        )

    report = apply_migrations(empty_database_url, migrations)

    assert report.applied_now == ("0003",)
    with psycopg.connect(empty_database_url) as conn:
        assert conn.execute(
            """
            SELECT chunking_version, indexing_version, ready
            FROM documents
            WHERE document_id = %s::uuid
            """,
            (document_id,),
        ).fetchone() == (
            "page-safe-dual-bound-v1:known",
            "legacy-document-index-v1:unknown-settings",
            True,
        )


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
    with pytest.raises(MigrationChecksumMismatch):
        inspect_migrations(empty_database_url, migrations)


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
    later_file = migrations / "0004_later.sql"
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
            "INSERT INTO schema_migrations (version, checksum) VALUES ('0004', %s)",
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

    assert report.applied_now == ("0001", "0002", "0003")
