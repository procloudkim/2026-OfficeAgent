"""Single-transaction PostgreSQL and pgvector corpus store."""

from __future__ import annotations

import hashlib
import math
import unicodedata
import uuid
from collections.abc import Sequence
from functools import partial

import anyio
import psycopg
from psycopg.types.json import Jsonb

from officeagent.domain.indexing import build_indexing_version
from officeagent.domain.models import (
    CachedAnswer,
    CacheVersions,
    CacheWriteResult,
    Chunk,
    Citation,
    DocumentChange,
    DocumentRecord,
    IngestionResult,
    PreparedChunk,
    RetrievalSnapshot,
    SearchHit,
)

_EMBEDDING_DIMENSION = 384
_DOCUMENT_NAMESPACE = uuid.UUID("9c02265a-7f75-4bf4-b398-b096430bf8eb")


class PostgresCorpusStore:
    """Own document, vector, cache, dependency, and revision state in one DB."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def commit_document(
        self,
        *,
        filename: str,
        content_type: str,
        content_hash: str,
        chunking_version: str,
        chunks: Sequence[PreparedChunk],
        embeddings: Sequence[Sequence[float]],
        cache_versions: CacheVersions,
        retrieval_threshold: float,
    ) -> IngestionResult:
        """Commit a fully prepared document and its invalidations atomically."""

        return await anyio.to_thread.run_sync(
            partial(
                self._commit_document,
                filename,
                content_type,
                content_hash,
                chunking_version,
                tuple(chunks),
                tuple(tuple(vector) for vector in embeddings),
                cache_versions,
                retrieval_threshold,
            )
        )

    async def retrieve(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> RetrievalSnapshot:
        """Read one revision-consistent exact pgvector retrieval snapshot."""

        return await anyio.to_thread.run_sync(
            partial(self._retrieve, tuple(query_embedding), limit)
        )

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[SearchHit]:
        """Compatibility read path; revision-aware callers should use retrieve()."""

        snapshot = await self.retrieve(query_embedding, limit=limit)
        return list(snapshot.hits)

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        """Return the ready document version."""

        return await anyio.to_thread.run_sync(partial(self._get_document, document_id))

    async def find_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        similarity_threshold: float,
        cache_versions: CacheVersions,
    ) -> CachedAnswer | None:
        """Soft-invalidate version drift, then prefer exact over similar cache."""

        return await anyio.to_thread.run_sync(
            partial(
                self._find_cached_answer,
                normalized_question,
                tuple(question_embedding),
                similarity_threshold,
                cache_versions,
            )
        )

    async def save_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        answer: str,
        citations: Sequence[Citation],
        cache_versions: CacheVersions,
        expected_revision: int,
    ) -> CacheWriteResult:
        """Write only when the corpus revision still matches retrieval."""

        return await anyio.to_thread.run_sync(
            partial(
                self._save_cached_answer,
                normalized_question,
                tuple(question_embedding),
                answer,
                tuple(citations),
                cache_versions,
                expected_revision,
            )
        )

    def _commit_document(
        self,
        filename: str,
        content_type: str,
        content_hash: str,
        chunking_version: str,
        chunks: tuple[PreparedChunk, ...],
        embeddings: tuple[tuple[float, ...], ...],
        cache_versions: CacheVersions,
        retrieval_threshold: float,
    ) -> IngestionResult:
        _validate_prepared_document(
            content_hash,
            chunking_version,
            chunks,
            embeddings,
            retrieval_threshold,
        )
        indexing_version = build_indexing_version(
            chunking_version=chunking_version,
            embedding_version=cache_versions.embedding_version,
        )
        vector_literals = tuple(_vector_literal(vector) for vector in embeddings)
        normalized_key = _normalize_document_key(filename)

        with psycopg.connect(self._database_url) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_advisory_lock_key(normalized_key),),
                )
                current = connection.execute(
                    """
                    SELECT document_id::text, version, content_hash,
                           chunking_version, indexing_version
                    FROM documents
                    WHERE normalized_key = %s AND ready
                    FOR UPDATE
                    """,
                    (normalized_key,),
                ).fetchone()

                if (
                    current is not None
                    and str(current[2]).strip() == content_hash
                    and str(current[3]) == chunking_version
                    and str(current[4]) == indexing_version
                ):
                    record = _document_record(
                        connection,
                        str(current[0]),
                        int(current[1]),
                    )
                    chunk_count = _document_chunk_count(
                        connection,
                        record.id,
                        record.version,
                    )
                    return IngestionResult(
                        document=record,
                        change=DocumentChange.UNCHANGED,
                        chunk_count=chunk_count,
                        invalidated_cache_entries=0,
                    )

                if current is None:
                    document_id = str(
                        uuid.uuid5(_DOCUMENT_NAMESPACE, normalized_key)
                    )
                    version = 1
                    change = DocumentChange.CREATED
                else:
                    document_id = str(current[0])
                    version = int(current[1]) + 1
                    change = DocumentChange.UPDATED
                chunking_changed = current is not None and str(current[3]) != chunking_version
                indexing_changed = current is not None and str(current[4]) != indexing_version

                connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, version, normalized_key, filename,
                        mime_type, content_hash, chunking_version,
                        indexing_version, ready
                    ) VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, false)
                    """,
                    (
                        document_id,
                        version,
                        normalized_key,
                        filename,
                        content_type,
                        content_hash,
                        chunking_version,
                        indexing_version,
                    ),
                )
                _executemany(connection,
                    """
                    INSERT INTO document_chunks (
                        document_id, document_version, chunk_index, content,
                        page, start_offset, end_offset, embedding
                    ) VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    [
                        (
                            document_id,
                            version,
                            chunk.chunk_index,
                            chunk.text,
                            chunk.page_number,
                            chunk.start_offset,
                            chunk.end_offset,
                            vector_literal,
                        )
                        for chunk, vector_literal in zip(
                            chunks,
                            vector_literals,
                            strict=True,
                        )
                    ],
                )

                invalidated = _invalidate_for_change(
                    connection,
                    change=change,
                    chunking_changed=chunking_changed,
                    indexing_changed=indexing_changed,
                    document_id=document_id,
                    document_version=version,
                    cache_versions=cache_versions,
                    retrieval_threshold=retrieval_threshold,
                )
                if current is not None:
                    connection.execute(
                        """
                        UPDATE documents
                        SET ready = false, updated_at = now()
                        WHERE document_id = %s::uuid AND version = %s
                        """,
                        (document_id, int(current[1])),
                    )
                connection.execute(
                    """
                    UPDATE documents
                    SET ready = true, updated_at = now()
                    WHERE document_id = %s::uuid AND version = %s
                    """,
                    (document_id, version),
                )
                connection.execute(
                    """
                    UPDATE corpus_state
                    SET revision = revision + 1, updated_at = now()
                    WHERE singleton
                    """
                )
                record = _document_record(connection, document_id, version)
                return IngestionResult(
                    document=record,
                    change=change,
                    chunk_count=len(chunks),
                    invalidated_cache_entries=invalidated,
                )

    def _retrieve(
        self,
        query_embedding: tuple[float, ...],
        limit: int,
    ) -> RetrievalSnapshot:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        vector_literal = _vector_literal(query_embedding)
        with psycopg.connect(self._database_url) as connection:
            with connection.transaction():
                revision = _corpus_revision(connection, lock_for_share=True)
                if _is_zero_vector(query_embedding):
                    return RetrievalSnapshot(revision=revision, hits=())
                rows = connection.execute(
                    """
                    SELECT
                        chunk.document_id::text,
                        chunk.document_version,
                        chunk.chunk_index,
                        document.filename,
                        chunk.content,
                        chunk.page,
                        chunk.start_offset,
                        chunk.end_offset,
                        1 - (chunk.embedding <=> %s::vector) AS score
                    FROM document_chunks AS chunk
                    JOIN documents AS document
                      ON (document.document_id, document.version) =
                         (chunk.document_id, chunk.document_version)
                    WHERE document.ready
                    ORDER BY chunk.embedding <=> %s::vector, chunk.document_id,
                             chunk.document_version, chunk.chunk_index
                    LIMIT %s
                    """,
                    (vector_literal, vector_literal, limit),
                ).fetchall()

        hits = tuple(_search_hit_from_row(row) for row in rows)
        return RetrievalSnapshot(revision=revision, hits=hits)

    def _get_document(self, document_id: str) -> DocumentRecord | None:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT document_id::text, filename, mime_type, content_hash,
                       version, created_at, updated_at
                FROM documents
                WHERE document_id = %s::uuid AND ready
                """,
                (document_id,),
            ).fetchone()
        return _document_record_from_row(row) if row is not None else None

    def _find_cached_answer(
        self,
        normalized_question: str,
        question_embedding: tuple[float, ...],
        similarity_threshold: float,
        cache_versions: CacheVersions,
    ) -> CachedAnswer | None:
        _validate_similarity_threshold(similarity_threshold)
        vector_literal = _vector_literal(question_embedding)
        versions = _cache_version_values(cache_versions)
        with psycopg.connect(self._database_url) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE answer_cache
                    SET active = false,
                        invalidated_at = now(),
                        invalidation_reason = 'version_mismatch'
                    WHERE active
                      AND NOT (
                          prompt_version = %s
                          AND embedding_version = %s
                          AND retrieval_version = %s
                      )
                    """,
                    versions,
                )
                row = connection.execute(
                    """
                    SELECT cache_id::text, normalized_question, answer,
                           citations, 1.0 AS similarity,
                           prompt_version, embedding_version, retrieval_version
                    FROM answer_cache
                    WHERE active
                      AND normalized_question = %s
                      AND prompt_version = %s
                      AND embedding_version = %s
                      AND retrieval_version = %s
                    LIMIT 1
                    """,
                    (normalized_question, *versions),
                ).fetchone()
                if row is None and not _is_zero_vector(question_embedding):
                    row = connection.execute(
                        """
                        SELECT cache_id::text, normalized_question, answer,
                               citations,
                               1 - (question_embedding <=> %s::vector) AS similarity,
                               prompt_version, embedding_version, retrieval_version
                        FROM answer_cache
                        WHERE active
                          AND prompt_version = %s
                          AND embedding_version = %s
                          AND retrieval_version = %s
                          AND 1 - (question_embedding <=> %s::vector) >= %s
                        ORDER BY question_embedding <=> %s::vector
                        LIMIT 1
                        """,
                        (
                            vector_literal,
                            *versions,
                            vector_literal,
                            similarity_threshold,
                            vector_literal,
                        ),
                    ).fetchone()
        return _cached_answer_from_row(row) if row is not None else None

    def _save_cached_answer(
        self,
        normalized_question: str,
        question_embedding: tuple[float, ...],
        answer: str,
        citations: tuple[Citation, ...],
        cache_versions: CacheVersions,
        expected_revision: int,
    ) -> CacheWriteResult:
        vector_literal = _vector_literal(question_embedding)
        versions = _cache_version_values(cache_versions)
        with psycopg.connect(self._database_url) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_advisory_lock_key(normalized_question),),
                )
                current_revision = _corpus_revision(
                    connection,
                    lock_for_update=True,
                )
                if current_revision != expected_revision:
                    return CacheWriteResult(
                        written=False,
                        skip_reason="corpus_changed",
                    )

                connection.execute(
                    """
                    UPDATE answer_cache
                    SET active = false,
                        invalidated_at = now(),
                        invalidation_reason = 'replaced_exact_cache'
                    WHERE active
                      AND normalized_question = %s
                      AND prompt_version = %s
                      AND embedding_version = %s
                      AND retrieval_version = %s
                    """,
                    (normalized_question, *versions),
                )
                cache_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO answer_cache (
                        cache_id, normalized_question, question_embedding,
                        answer, citations, prompt_version, embedding_version,
                        retrieval_version, created_revision
                    ) VALUES (
                        %s::uuid, %s, %s::vector, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        cache_id,
                        normalized_question,
                        vector_literal,
                        answer,
                        Jsonb([_citation_mapping(citation) for citation in citations]),
                        *versions,
                        expected_revision,
                    ),
                )
                dependencies = {
                    (
                        citation.document_id,
                        citation.document_version,
                    )
                    for citation in citations
                }
                if dependencies:
                    _executemany(connection,
                        """
                        INSERT INTO cache_dependencies (
                            cache_id, document_id, document_version
                        ) VALUES (%s::uuid, %s::uuid, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            (cache_id, document_id, document_version)
                            for document_id, document_version in dependencies
                        ],
                    )
                return CacheWriteResult(
                    written=True,
                    cache_id=cache_id,
                )


def _invalidate_for_change(
    connection: psycopg.Connection,
    *,
    change: DocumentChange,
    chunking_changed: bool,
    indexing_changed: bool,
    document_id: str,
    document_version: int,
    cache_versions: CacheVersions,
    retrieval_threshold: float,
) -> int:
    if chunking_changed:
        rows = connection.execute(
            """
            UPDATE answer_cache
            SET active = false,
                invalidated_at = now(),
                invalidation_reason = 'chunking_configuration_changed'
            WHERE active
            RETURNING cache_id
            """
        ).fetchall()
        return len(rows)

    if indexing_changed:
        rows = connection.execute(
            """
            UPDATE answer_cache
            SET active = false,
                invalidated_at = now(),
                invalidation_reason = 'indexing_configuration_changed'
            WHERE active
            RETURNING cache_id
            """
        ).fetchall()
        return len(rows)

    if change is DocumentChange.CREATED:
        rows = connection.execute(
            """
            UPDATE answer_cache
            SET active = false,
                invalidated_at = now(),
                invalidation_reason = 'corpus_expanded'
            WHERE active
            RETURNING cache_id
            """
        ).fetchall()
        return len(rows)

    incompatible = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM answer_cache
            WHERE active
              AND (
                  prompt_version <> %s
                  OR embedding_version <> %s
                  OR retrieval_version <> %s
              )
        )
        """,
        (
            cache_versions.prompt_version,
            cache_versions.embedding_version,
            cache_versions.retrieval_version,
        ),
    ).fetchone()
    if incompatible is not None and bool(incompatible[0]):
        rows = connection.execute(
            """
            UPDATE answer_cache
            SET active = false,
                invalidated_at = now(),
                invalidation_reason = 'configuration_mismatch'
            WHERE active
            RETURNING cache_id
            """
        ).fetchall()
        return len(rows)

    dependency_rows = connection.execute(
        """
        UPDATE answer_cache AS cache
        SET active = false,
            invalidated_at = now(),
            invalidation_reason = 'document_updated'
        WHERE cache.active
          AND EXISTS (
              SELECT 1
              FROM cache_dependencies AS dependency
              WHERE dependency.cache_id = cache.cache_id
                AND dependency.document_id = %s::uuid
          )
        RETURNING cache.cache_id
        """,
        (document_id,),
    ).fetchall()
    similarity_rows = connection.execute(
        """
        UPDATE answer_cache AS cache
        SET active = false,
            invalidated_at = now(),
            invalidation_reason = 'new_evidence_relevant'
        WHERE cache.active
          AND EXISTS (
              SELECT 1
              FROM document_chunks AS chunk
              WHERE chunk.document_id = %s::uuid
                AND chunk.document_version = %s
                AND 1 - (chunk.embedding <=> cache.question_embedding) >= %s
          )
        RETURNING cache.cache_id
        """,
        (document_id, document_version, retrieval_threshold),
    ).fetchall()
    return len(dependency_rows) + len(similarity_rows)


def _executemany(
    connection: psycopg.Connection,
    statement: str,
    rows: Sequence[Sequence[object]],
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


def _validate_prepared_document(
    content_hash: str,
    chunking_version: str,
    chunks: tuple[PreparedChunk, ...],
    embeddings: tuple[tuple[float, ...], ...],
    retrieval_threshold: float,
) -> None:
    if len(content_hash) != 64 or any(
        character not in "0123456789abcdef" for character in content_hash.casefold()
    ):
        raise ValueError("content_hash must be a hexadecimal SHA-256 digest")
    if (
        not chunking_version.strip()
        or len(chunking_version) > 256
        or any(character in chunking_version for character in ("\n", "\r", "\x00"))
    ):
        raise ValueError("chunking_version must be a bounded non-blank identifier")
    if not chunks:
        raise ValueError("at least one prepared chunk is required")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have matching lengths")
    if not -1.0 <= retrieval_threshold <= 1.0:
        raise ValueError("retrieval_threshold must be between -1 and 1")


def _validate_similarity_threshold(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("similarity_threshold must be between 0 and 1")


def _normalize_document_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _advisory_lock_key(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def _vector_literal(values: Sequence[float]) -> str:
    vector = tuple(float(value) for value in values)
    if len(vector) != _EMBEDDING_DIMENSION:
        raise ValueError("expected 384 embedding dimensions")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding values must be finite")
    return "[" + ",".join(format(value, ".17g") for value in vector) + "]"


def _is_zero_vector(values: Sequence[float]) -> bool:
    return all(float(value) == 0.0 for value in values)


def _cache_version_values(versions: CacheVersions) -> tuple[str, str, str]:
    return (
        versions.prompt_version,
        versions.embedding_version,
        versions.retrieval_version,
    )


def _corpus_revision(
    connection: psycopg.Connection,
    *,
    lock_for_share: bool = False,
    lock_for_update: bool = False,
) -> int:
    if lock_for_share and lock_for_update:
        raise ValueError("select only one corpus revision lock mode")
    suffix = " FOR SHARE" if lock_for_share else " FOR UPDATE" if lock_for_update else ""
    row = connection.execute(
        f"SELECT revision FROM corpus_state WHERE singleton{suffix}"
    ).fetchone()
    if row is None:
        raise RuntimeError("database migrations are not applied")
    return int(row[0])


def _document_record(
    connection: psycopg.Connection,
    document_id: str,
    version: int,
) -> DocumentRecord:
    row = connection.execute(
        """
        SELECT document_id::text, filename, mime_type, content_hash,
               version, created_at, updated_at
        FROM documents
        WHERE document_id = %s::uuid AND version = %s
        """,
        (document_id, version),
    ).fetchone()
    if row is None:
        raise RuntimeError("document row disappeared inside its transaction")
    return _document_record_from_row(row)


def _document_record_from_row(row: Sequence[object]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row[0]),
        filename=str(row[1]),
        content_type=str(row[2]),
        content_hash=str(row[3]).strip(),
        version=int(row[4]),
        created_at=row[5],
        updated_at=row[6],
    )


def _document_chunk_count(
    connection: psycopg.Connection,
    document_id: str,
    version: int,
) -> int:
    row = connection.execute(
        """
        SELECT count(*)
        FROM document_chunks
        WHERE document_id = %s::uuid AND document_version = %s
        """,
        (document_id, version),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _search_hit_from_row(row: Sequence[object]) -> SearchHit:
    document_id = str(row[0])
    document_version = int(row[1])
    chunk_index = int(row[2])
    text = str(row[4])
    start_offset = int(row[6])
    end_offset = int(row[7])
    identity = (
        f"{document_id}:{document_version}:{chunk_index}:"
        f"{start_offset}:{end_offset}:{text}"
    )
    chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return SearchHit(
        chunk=Chunk(
            id=chunk_id,
            document_id=document_id,
            document_version=document_version,
            filename=str(row[3]),
            text=text,
            chunk_index=chunk_index,
            page_number=int(row[5]) if row[5] is not None else None,
            start_offset=start_offset,
            end_offset=end_offset,
        ),
        score=float(row[8]),
    )


def _cached_answer_from_row(row: Sequence[object]) -> CachedAnswer:
    raw_citations = row[3]
    citations = tuple(
        Citation(**dict(item))
        for item in raw_citations
    )
    return CachedAnswer(
        id=str(row[0]),
        normalized_question=str(row[1]),
        answer=str(row[2]),
        citations=citations,
        similarity=float(row[4]),
        versions=CacheVersions(
            prompt_version=str(row[5]),
            embedding_version=str(row[6]),
            retrieval_version=str(row[7]),
        ),
    )


def _citation_mapping(citation: Citation) -> dict[str, object]:
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
