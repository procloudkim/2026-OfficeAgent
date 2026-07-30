import sqlite3

import pytest

from officeagent.domain.models import CacheVersions, Citation, DocumentChange
from officeagent.infrastructure.sqlite_store import SqliteMetadataStore

_VERSIONS = CacheVersions(
    prompt_version="prompt-v1",
    embedding_version="embedding-v1",
    retrieval_version="retrieval-v1",
)



def _citation(document_id: str) -> Citation:
    return Citation(
        document_id=document_id,
        document_version=1,
        filename=f"{document_id}.txt",
        chunk_id=f"{document_id}:0",
        chunk_index=0,
        page_number=None,
        start_offset=0,
        end_offset=10,
        quote="evidence",
        score=0.9,
    )


async def test_document_version_changes_only_when_hash_changes(test_data_dir) -> None:
    store = SqliteMetadataStore(test_data_dir / "metadata.sqlite3")
    await store.initialize()

    created = await store.upsert_document(
        filename="policy.txt", content_type="text/plain", content_hash="hash-a"
    )
    unchanged = await store.upsert_document(
        filename="policy.txt", content_type="text/plain", content_hash="hash-a"
    )
    updated = await store.upsert_document(
        filename="policy.txt", content_type="text/plain", content_hash="hash-b"
    )

    assert created.change is DocumentChange.CREATED
    assert unchanged.change is DocumentChange.UNCHANGED
    assert updated.change is DocumentChange.UPDATED
    assert created.document.id == unchanged.document.id == updated.document.id
async def test_cache_prefers_exact_then_invalidates_only_dependencies(
    test_data_dir,
) -> None:
    store = SqliteMetadataStore(test_data_dir / "metadata.sqlite3")
    await store.initialize()
    first_document = await store.upsert_document(
        filename="first.txt", content_type="text/plain", content_hash="first"
    )
    second_document = await store.upsert_document(
        filename="second.txt", content_type="text/plain", content_hash="second"
    )
    await store.save_cached_answer(
        normalized_question="what is first",
        question_embedding=[1.0, 0.0],
        answer="first answer",
        citations=[_citation(first_document.document.id)],
        cache_versions=_VERSIONS,
    )
    await store.save_cached_answer(
        normalized_question="what is second",
        question_embedding=[0.0, 1.0],
        answer="second answer",
        citations=[_citation(second_document.document.id)],
        cache_versions=_VERSIONS,
    )

    exact = await store.find_cached_answer(
        normalized_question="what is first",
        question_embedding=[0.0, 1.0],
        similarity_threshold=0.99,
        cache_versions=_VERSIONS,
    )
    invalidated = await store.invalidate_cache_for_document(first_document.document.id)
    remaining = await store.find_cached_answer(
        normalized_question="what is second",
        question_embedding=[0.0, 1.0],
        similarity_threshold=0.99,
        cache_versions=_VERSIONS,
    )

    assert exact is not None and exact.answer == "first answer" and exact.similarity == 1.0
    assert exact.versions == _VERSIONS
    assert invalidated == 1
    assert remaining is not None and remaining.answer == "second answer"
    assert await store.invalidate_all_cache() == 1


async def test_cache_misses_when_any_semantic_version_differs(test_data_dir) -> None:
    store = SqliteMetadataStore(test_data_dir / "metadata.sqlite3")
    await store.initialize()
    document = await store.upsert_document(
        filename="policy.txt", content_type="text/plain", content_hash="policy"
    )
    mismatches = (
        CacheVersions("prompt-v2", "embedding-v1", "retrieval-v1"),
        CacheVersions("prompt-v1", "embedding-v2", "retrieval-v1"),
        CacheVersions("prompt-v1", "embedding-v1", "retrieval-v2"),
    )

    for mismatch in mismatches:
        await store.save_cached_answer(
            normalized_question="policy question",
            question_embedding=[1.0, 0.0],
            answer="versioned answer",
            citations=[_citation(document.document.id)],
            cache_versions=_VERSIONS,
        )
        exact = await store.find_cached_answer(
            normalized_question="policy question",
            question_embedding=[1.0, 0.0],
            similarity_threshold=0.95,
            cache_versions=mismatch,
        )
        similar = await store.find_cached_answer(
            normalized_question="similar wording",
            question_embedding=[1.0, 0.0],
            similarity_threshold=0.95,
            cache_versions=mismatch,
        )
        assert exact is None
        assert similar is None


async def test_cache_dependency_rejects_unknown_document(test_data_dir) -> None:
    store = SqliteMetadataStore(test_data_dir / "metadata.sqlite3")
    await store.initialize()

    with pytest.raises(sqlite3.IntegrityError):
        await store.save_cached_answer(
            normalized_question="unknown",
            question_embedding=[1.0, 0.0],
            answer="must not persist",
            citations=[_citation("missing-document")],
            cache_versions=_VERSIONS,
        )
