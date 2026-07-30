"""Real PostgreSQL/pgvector transaction and cache invariants."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator

import psycopg
import pytest

from officeagent.domain.models import (
    CacheVersions,
    Citation,
    DocumentChange,
    PreparedChunk,
)
from officeagent.infrastructure.postgres_corpus_store import PostgresCorpusStore

DATABASE_URL = os.environ.get("OFFICEAGENT_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the migrated Compose test database",
)

_VERSIONS = CacheVersions(
    prompt_version="prompt-v1",
    embedding_version="embedding-384-v1",
    retrieval_version="cosine-v1",
)


@pytest.fixture(autouse=True)
def clean_postgres_corpus() -> Iterator[None]:
    if DATABASE_URL is None:
        yield
        return
    _reset_database()
    try:
        yield
    finally:
        _reset_database()


def _reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            TRUNCATE cache_dependencies, answer_cache, document_chunks, documents
            CASCADE
            """
        )
        connection.execute(
            "UPDATE corpus_state SET revision = 0, updated_at = now() WHERE singleton"
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _vector(index: int) -> list[float]:
    values = [0.0] * 384
    values[index] = 1.0
    return values


def _chunk(text: str) -> PreparedChunk:
    return PreparedChunk(
        text=text,
        chunk_index=0,
        page_number=None,
        start_offset=0,
        end_offset=len(text),
    )


def _citation(hit) -> Citation:
    chunk = hit.chunk
    return Citation(
        document_id=chunk.document_id,
        document_version=chunk.document_version,
        filename=chunk.filename,
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        page_number=chunk.page_number,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        quote=chunk.text,
        score=hit.score,
    )


async def _commit(
    store: PostgresCorpusStore,
    *,
    filename: str,
    content: str,
    vector_index: int,
):
    return await store.commit_document(
        filename=filename,
        content_type="text/plain",
        content_hash=_digest(content),
        chunks=[_chunk(content)],
        embeddings=[_vector(vector_index)],
        cache_versions=_VERSIONS,
        retrieval_threshold=0.30,
    )


async def test_commit_is_atomic_and_pgvector_retrieval_tracks_ready_version() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)

    created = await _commit(
        store,
        filename="policy.txt",
        content="education allowance is 100",
        vector_index=0,
    )
    first_snapshot = await store.retrieve(_vector(0), limit=3)
    unchanged = await _commit(
        store,
        filename="policy.txt",
        content="education allowance is 100",
        vector_index=0,
    )
    updated = await _commit(
        store,
        filename="policy.txt",
        content="deployment approval requires two reviewers",
        vector_index=1,
    )
    second_snapshot = await store.retrieve(_vector(1), limit=3)

    assert created.change is DocumentChange.CREATED
    assert unchanged.change is DocumentChange.UNCHANGED
    assert updated.change is DocumentChange.UPDATED
    assert (created.document.version, unchanged.document.version, updated.document.version) == (
        1,
        1,
        2,
    )
    assert first_snapshot.revision == 1
    assert second_snapshot.revision == 2
    assert first_snapshot.hits[0].chunk.filename == "policy.txt"
    assert second_snapshot.hits[0].chunk.document_version == 2
    assert second_snapshot.hits[0].score == pytest.approx(1.0)

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM documents WHERE ready"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT revision FROM corpus_state WHERE singleton"
        ).fetchone() == (2,)


async def test_cache_requires_all_versions_and_revision_guard_skips_stale_write() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)
    await _commit(
        store,
        filename="benefits.txt",
        content="education benefit is 200",
        vector_index=0,
    )
    snapshot = await store.retrieve(_vector(0), limit=3)
    citation = _citation(snapshot.hits[0])

    written = await store.save_cached_answer(
        normalized_question="education benefit",
        question_embedding=_vector(0),
        answer="200",
        citations=[citation],
        cache_versions=_VERSIONS,
        expected_revision=snapshot.revision,
    )
    exact = await store.find_cached_answer(
        normalized_question="education benefit",
        question_embedding=_vector(0),
        similarity_threshold=0.95,
        cache_versions=_VERSIONS,
    )
    similar = await store.find_cached_answer(
        normalized_question="benefit details",
        question_embedding=_vector(0),
        similarity_threshold=0.95,
        cache_versions=_VERSIONS,
    )

    assert written.written is True
    assert written.cache_id is not None
    assert exact is not None and exact.similarity == 1.0
    assert similar is not None and similar.similarity == pytest.approx(1.0)
    assert exact.versions == _VERSIONS

    mismatches = (
        CacheVersions("prompt-v2", "embedding-384-v1", "cosine-v1"),
        CacheVersions("prompt-v1", "embedding-384-v2", "cosine-v1"),
        CacheVersions("prompt-v1", "embedding-384-v1", "cosine-v2"),
    )
    for mismatch in mismatches:
        await store.save_cached_answer(
            normalized_question="education benefit",
            question_embedding=_vector(0),
            answer="200",
            citations=[citation],
            cache_versions=_VERSIONS,
            expected_revision=snapshot.revision,
        )
        miss = await store.find_cached_answer(
            normalized_question="education benefit",
            question_embedding=_vector(0),
            similarity_threshold=0.95,
            cache_versions=mismatch,
        )
        assert miss is None

    await _commit(
        store,
        filename="new-policy.txt",
        content="security policy",
        vector_index=2,
    )
    stale_write = await store.save_cached_answer(
        normalized_question="education benefit",
        question_embedding=_vector(0),
        answer="stale",
        citations=[citation],
        cache_versions=_VERSIONS,
        expected_revision=snapshot.revision,
    )
    assert stale_write.written is False
    assert stale_write.skip_reason == "corpus_changed"


async def test_update_invalidates_newly_relevant_non_dependency_cache() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)
    await _commit(
        store,
        filename="benefits.txt",
        content="education allowance",
        vector_index=0,
    )
    await _commit(
        store,
        filename="deployment.txt",
        content="deployment reviewers",
        vector_index=2,
    )
    snapshot = await store.retrieve(_vector(0), limit=1)
    await store.save_cached_answer(
        normalized_question="security question",
        question_embedding=_vector(1),
        answer="old answer",
        citations=[_citation(snapshot.hits[0])],
        cache_versions=_VERSIONS,
        expected_revision=snapshot.revision,
    )

    result = await _commit(
        store,
        filename="deployment.txt",
        content="security policy now applies",
        vector_index=1,
    )

    assert result.change is DocumentChange.UPDATED
    assert result.invalidated_cache_entries == 1
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT active, invalidation_reason FROM answer_cache"
        ).fetchone()
    assert row == (False, "new_evidence_relevant")


async def test_chunk_failure_rolls_back_document_and_revision() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)
    created = await _commit(
        store,
        filename="rollback.txt",
        content="education baseline",
        vector_index=0,
    )
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            CREATE FUNCTION officeagent_fail_chunk() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.content LIKE 'FAIL%' THEN
                    RAISE EXCEPTION 'forced chunk failure';
                END IF;
                RETURN NEW;
            END
            $$
            """
        )
        connection.execute(
            """
            CREATE TRIGGER officeagent_fail_chunk_trigger
            BEFORE INSERT ON document_chunks
            FOR EACH ROW EXECUTE FUNCTION officeagent_fail_chunk()
            """
        )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="forced chunk failure"):
            await _commit(
                store,
                filename="rollback.txt",
                content="FAIL updated content",
                vector_index=1,
            )
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "DROP TRIGGER IF EXISTS officeagent_fail_chunk_trigger ON document_chunks"
            )
            connection.execute("DROP FUNCTION IF EXISTS officeagent_fail_chunk()")

    ready = await store.get_document(created.document.id)
    assert ready is not None and ready.version == 1
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM documents WHERE document_id = %s::uuid",
            (created.document.id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT revision FROM corpus_state WHERE singleton"
        ).fetchone() == (1,)


async def test_same_document_concurrency_serializes_to_one_revision() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)

    first, second = await asyncio.gather(
        _commit(
            store,
            filename="concurrent.txt",
            content="one stable document",
            vector_index=0,
        ),
        _commit(
            store,
            filename="concurrent.txt",
            content="one stable document",
            vector_index=0,
        ),
    )

    assert {first.change, second.change} == {
        DocumentChange.CREATED,
        DocumentChange.UNCHANGED,
    }
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM documents WHERE ready"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT revision FROM corpus_state WHERE singleton"
        ).fetchone() == (1,)

async def test_prompt_version_change_uses_full_invalidation_fallback() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)
    await _commit(
        store,
        filename="prompt-policy.txt",
        content="education baseline",
        vector_index=0,
    )
    snapshot = await store.retrieve(_vector(0), limit=1)
    await store.save_cached_answer(
        normalized_question="education baseline",
        question_embedding=_vector(0),
        answer="baseline",
        citations=[_citation(snapshot.hits[0])],
        cache_versions=_VERSIONS,
        expected_revision=snapshot.revision,
    )
    next_versions = CacheVersions(
        prompt_version="prompt-v2",
        embedding_version=_VERSIONS.embedding_version,
        retrieval_version=_VERSIONS.retrieval_version,
    )

    updated = await store.commit_document(
        filename="prompt-policy.txt",
        content_type="text/plain",
        content_hash=_digest("education updated"),
        chunks=[_chunk("education updated")],
        embeddings=[_vector(0)],
        cache_versions=next_versions,
        retrieval_threshold=0.30,
    )

    assert updated.change is DocumentChange.UPDATED
    assert updated.invalidated_cache_entries == 1
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT active, invalidation_reason FROM answer_cache"
        ).fetchone()
    assert row == (False, "configuration_mismatch")


async def test_failure_after_cache_invalidation_rolls_back_every_write() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)
    created = await _commit(
        store,
        filename="atomic-update.txt",
        content="education baseline",
        vector_index=0,
    )
    snapshot = await store.retrieve(_vector(0), limit=1)
    await store.save_cached_answer(
        normalized_question="education baseline",
        question_embedding=_vector(0),
        answer="baseline",
        citations=[_citation(snapshot.hits[0])],
        cache_versions=_VERSIONS,
        expected_revision=snapshot.revision,
    )
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            CREATE FUNCTION officeagent_fail_revision() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'forced revision failure';
            END
            $$
            """
        )
        connection.execute(
            """
            CREATE TRIGGER officeagent_fail_revision_trigger
            BEFORE UPDATE ON corpus_state
            FOR EACH ROW EXECUTE FUNCTION officeagent_fail_revision()
            """
        )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="forced revision failure"):
            await _commit(
                store,
                filename="atomic-update.txt",
                content="education updated",
                vector_index=1,
            )
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "DROP TRIGGER IF EXISTS officeagent_fail_revision_trigger ON corpus_state"
            )
            connection.execute("DROP FUNCTION IF EXISTS officeagent_fail_revision()")

    ready = await store.get_document(created.document.id)
    assert ready is not None and ready.version == 1
    with psycopg.connect(DATABASE_URL) as connection:
        documents = connection.execute(
            """
            SELECT version, ready
            FROM documents
            WHERE document_id = %s::uuid
            ORDER BY version
            """,
            (created.document.id,),
        ).fetchall()
        chunks = connection.execute(
            """
            SELECT document_version, content
            FROM document_chunks
            WHERE document_id = %s::uuid
            ORDER BY document_version, chunk_index
            """,
            (created.document.id,),
        ).fetchall()
        cache = connection.execute(
            "SELECT active, invalidation_reason FROM answer_cache"
        ).fetchone()
        revision = connection.execute(
            "SELECT revision FROM corpus_state WHERE singleton"
        ).fetchone()

    assert documents == [(1, True)]
    assert chunks == [(1, "education baseline")]
    assert cache == (True, None)
    assert revision == (1,)


async def test_distinct_document_concurrency_preserves_both_revision_increments() -> None:
    assert DATABASE_URL is not None
    store = PostgresCorpusStore(DATABASE_URL)

    first, second = await asyncio.gather(
        _commit(
            store,
            filename="concurrent-a.txt",
            content="education policy",
            vector_index=0,
        ),
        _commit(
            store,
            filename="concurrent-b.txt",
            content="deployment policy",
            vector_index=1,
        ),
    )

    assert first.change is DocumentChange.CREATED
    assert second.change is DocumentChange.CREATED
    with psycopg.connect(DATABASE_URL) as connection:
        ready_count = connection.execute(
            "SELECT count(*) FROM documents WHERE ready"
        ).fetchone()
        revision = connection.execute(
            "SELECT revision FROM corpus_state WHERE singleton"
        ).fetchone()

    assert ready_count == (2,)
    assert revision == (2,)
