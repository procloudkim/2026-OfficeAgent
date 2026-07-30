"""SQLite-backed document metadata and answer-cache storage."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from officeagent.domain.models import (
    CachedAnswer,
    CacheVersions,
    Citation,
    DocumentChange,
    DocumentRecord,
    DocumentUpsertResult,
)

_DOCUMENT_NAMESPACE = uuid.UUID("e641c250-019c-4915-9160-58a4a38cbe07")


class SqliteMetadataStore:
    """Keep stable document identities and cache entries in one local SQLite file."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    async def initialize(self) -> None:
        """Create the schema without changing existing document or cache state."""

        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS answer_cache (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    normalized_question TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    prompt_version TEXT NOT NULL DEFAULT 'legacy-unknown-v0',
                    embedding_version TEXT NOT NULL DEFAULT 'legacy-unknown-v0',
                    retrieval_version TEXT NOT NULL DEFAULT 'legacy-unknown-v0',
                    valid INTEGER NOT NULL DEFAULT 1 CHECK (valid IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_answer_cache_exact
                ON answer_cache(normalized_question, valid, updated_at DESC);

                CREATE TABLE IF NOT EXISTS cache_dependencies (
                    cache_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    PRIMARY KEY (cache_id, document_id),
                    FOREIGN KEY (cache_id) REFERENCES answer_cache(id) ON DELETE CASCADE,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_cache_dependencies_document
                ON cache_dependencies(document_id, cache_id);
                """
            )
            await _ensure_cache_version_columns(connection)
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_answer_cache_exact_versions
                ON answer_cache(normalized_question, prompt_version,
                    embedding_version, retrieval_version, valid, updated_at DESC)
                """
            )
            await connection.commit()

    async def upsert_document(
        self,
        *,
        filename: str,
        content_type: str,
        content_hash: str,
    ) -> DocumentUpsertResult:
        """Use filename as the stable identity and increment only on content changes."""

        now = _utc_now()
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM documents WHERE filename = ?", (filename,)
            )
            existing = await cursor.fetchone()
            if existing is None:
                document_id = str(uuid.uuid5(_DOCUMENT_NAMESPACE, filename))
                await connection.execute(
                    """
                    INSERT INTO documents (
                        id, filename, content_type, content_hash, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (document_id, filename, content_type, content_hash, now, now),
                )
                await connection.commit()
                return DocumentUpsertResult(
                    document=DocumentRecord(
                        id=document_id,
                        filename=filename,
                        content_type=content_type,
                        content_hash=content_hash,
                        version=1,
                        created_at=_parse_datetime(now),
                        updated_at=_parse_datetime(now),
                    ),
                    change=DocumentChange.CREATED,
                )

            record = _document_from_row(existing)
            if record.content_hash == content_hash:
                return DocumentUpsertResult(document=record, change=DocumentChange.UNCHANGED)

            next_version = record.version + 1
            await connection.execute(
                """
                UPDATE documents
                SET content_type = ?, content_hash = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (content_type, content_hash, next_version, now, record.id),
            )
            await connection.commit()
            return DocumentUpsertResult(
                document=DocumentRecord(
                    id=record.id,
                    filename=record.filename,
                    content_type=content_type,
                    content_hash=content_hash,
                    version=next_version,
                    created_at=record.created_at,
                    updated_at=_parse_datetime(now),
                ),
                change=DocumentChange.UPDATED,
            )

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        """Return the current version of a document, if it exists."""

        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            )
            row = await cursor.fetchone()
        return _document_from_row(row) if row is not None else None

    async def find_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        similarity_threshold: float,
        cache_versions: CacheVersions,
    ) -> CachedAnswer | None:
        """Return only cache entries produced by the current semantic versions."""

        _validate_threshold(similarity_threshold)
        query_vector = _as_vector(question_embedding)
        versions = _cache_version_values(cache_versions)
        now = _utc_now()
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute(
                """
                UPDATE answer_cache
                SET valid = 0, updated_at = ?
                WHERE valid = 1
                  AND (prompt_version <> ? OR embedding_version <> ?
                       OR retrieval_version <> ?)
                """,
                (now, *versions),
            )
            cursor = await connection.execute(
                """
                SELECT * FROM answer_cache
                WHERE valid = 1 AND normalized_question = ?
                  AND prompt_version = ? AND embedding_version = ?
                  AND retrieval_version = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (normalized_question, *versions),
            )
            exact = await cursor.fetchone()
            if exact is not None:
                await connection.commit()
                return _cached_answer_from_row(exact, similarity=1.0)

            cursor = await connection.execute(
                """
                SELECT * FROM answer_cache
                WHERE valid = 1 AND prompt_version = ?
                  AND embedding_version = ? AND retrieval_version = ?
                ORDER BY updated_at DESC
                """,
                versions,
            )
            rows = await cursor.fetchall()
            await connection.commit()

        best_row: sqlite3.Row | None = None
        best_similarity = similarity_threshold
        for row in rows:
            cache_vector = _as_vector(json.loads(row["embedding_json"]))
            similarity = _cosine_similarity(query_vector, cache_vector)
            if similarity >= best_similarity:
                best_row = row
                best_similarity = similarity

        if best_row is None:
            return None
        return _cached_answer_from_row(best_row, similarity=best_similarity)

    async def save_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        answer: str,
        citations: Sequence[Citation],
        cache_versions: CacheVersions,
    ) -> str:
        """Save one answer under its full semantic computation identity."""

        cache_id = str(uuid.uuid4())
        now = _utc_now()
        versions = _cache_version_values(cache_versions)
        vector_json = json.dumps(_as_vector(question_embedding), separators=(",", ":"))
        citations_json = json.dumps(
            [_citation_to_mapping(citation) for citation in citations], separators=(",", ":")
        )
        document_ids = {citation.document_id for citation in citations}

        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute(
                """
                UPDATE answer_cache SET valid = 0, updated_at = ?
                WHERE valid = 1 AND normalized_question = ?
                  AND prompt_version = ? AND embedding_version = ?
                  AND retrieval_version = ?
                """,
                (now, normalized_question, *versions),
            )
            await connection.execute(
                """
                INSERT INTO answer_cache (
                    id, question, normalized_question, embedding_json, answer,
                    citations_json, prompt_version, embedding_version,
                    retrieval_version, valid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    cache_id,
                    normalized_question,
                    normalized_question,
                    vector_json,
                    answer,
                    citations_json,
                    *versions,
                    now,
                    now,
                ),
            )
            if document_ids:
                await connection.executemany(
                    "INSERT INTO cache_dependencies (cache_id, document_id) VALUES (?, ?)",
                    [(cache_id, document_id) for document_id in document_ids],
                )
            await connection.commit()
        return cache_id

    async def invalidate_cache_for_document(self, document_id: str) -> int:
        """Mark active cache entries citing one document invalid and return its row count."""

        now = _utc_now()
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                UPDATE answer_cache
                SET valid = 0, updated_at = ?
                WHERE valid = 1
                  AND EXISTS (
                      SELECT 1 FROM cache_dependencies dependency
                      WHERE dependency.cache_id = answer_cache.id
                        AND dependency.document_id = ?
                  )
                """,
                (now, document_id),
            )
            await connection.commit()
            return cursor.rowcount

    async def invalidate_all_cache(self) -> int:
        """Mark every active cache entry invalid and return the affected row count."""

        now = _utc_now()
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "UPDATE answer_cache SET valid = 0, updated_at = ? WHERE valid = 1", (now,)
            )
            await connection.commit()
            return cursor.rowcount

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open one connection with SQLite's connection-scoped FK checks enabled."""

        connection = await aiosqlite.connect(self._database_path)
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            await connection.close()


async def _ensure_cache_version_columns(connection: aiosqlite.Connection) -> None:
    """Add P0 cache identity columns to an existing V1 SQLite database."""

    cursor = await connection.execute("PRAGMA table_info(answer_cache)")
    existing = {str(row[1]) for row in await cursor.fetchall()}
    statements = {
        "prompt_version": "ALTER TABLE answer_cache ADD COLUMN prompt_version "
        "TEXT NOT NULL DEFAULT 'legacy-unknown-v0'",
        "embedding_version": "ALTER TABLE answer_cache ADD COLUMN embedding_version "
        "TEXT NOT NULL DEFAULT 'legacy-unknown-v0'",
        "retrieval_version": "ALTER TABLE answer_cache ADD COLUMN retrieval_version "
        "TEXT NOT NULL DEFAULT 'legacy-unknown-v0'",
    }
    for column, statement in statements.items():
        if column not in existing:
            await connection.execute(statement)

def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _document_from_row(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        id=row["id"],
        filename=row["filename"],
        content_type=row["content_type"],
        content_hash=row["content_hash"],
        version=row["version"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _cached_answer_from_row(row: sqlite3.Row, *, similarity: float) -> CachedAnswer:
    citation_mappings = json.loads(row["citations_json"])
    citations = tuple(Citation(**citation) for citation in citation_mappings)
    return CachedAnswer(
        id=row["id"],
        normalized_question=row["normalized_question"],
        answer=row["answer"],
        citations=citations,
        similarity=similarity,
        versions=CacheVersions(
            prompt_version=row["prompt_version"],
            embedding_version=row["embedding_version"],
            retrieval_version=row["retrieval_version"],
        ),
    )


def _citation_to_mapping(citation: Citation) -> dict[str, object]:
    return {
        "document_id": citation.document_id,
        "document_version": citation.document_version,
        "filename": citation.filename,
        "chunk_id": citation.chunk_id,
        "chunk_index": citation.chunk_index,
        "page_number": citation.page_number,
        "start_offset": citation.start_offset,
        "end_offset": citation.end_offset,
        "quote": citation.quote,
        "score": citation.score,
    }


def _cache_version_values(versions: CacheVersions) -> tuple[str, str, str]:
    return (
        versions.prompt_version,
        versions.embedding_version,
        versions.retrieval_version,
    )


def _validate_threshold(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("similarity_threshold must be between 0 and 1")


def _as_vector(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in values]
    if not vector:
        raise ValueError("embedding vectors must not be empty")
    return vector


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
