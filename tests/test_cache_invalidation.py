"""Required cache path: exact/similar reuse and dependency invalidation."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from officeagent.application.ingestion import IngestionService
from officeagent.application.question_answering import normalize_question
from officeagent.domain.models import Chunk, DocumentChange, SearchHit
from officeagent.domain.ports import EmbeddingProvider, VectorStore
from officeagent.infrastructure.chroma_store import ChromaVectorStore
from tests.helpers import (
    FakeLlmGateway,
    KeywordEmbeddingProvider,
    build_test_runtime,
)
from tests.helpers import TestRuntime as RuntimeFixture

_RELATED_QUESTION = "Explain the education allowance"
_SIMILAR_RELATED_QUESTION = "Education allowance details, please"
_UNRELATED_QUESTION = "Explain the deployment rule"
_ORIGINAL_CONTENT = b"Education allowance is 100 dollars."
_UPDATED_CONTENT = b"Education allowance is now 200 dollars."
_CREATED_FAILURE_QUESTION = "policy current"
_SIMILAR_CREATED_FAILURE_QUESTION = "current policy"
_LEGACY_CORPUS_CONTENT = b"Policy legacy answer is 100 dollars."
_CURRENT_CORPUS_CONTENT = b"Policy current current answer is 200 dollars."


class _CorpusAwareLlmGateway:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream_answer(self, prompt: str):
        self.prompts.append(prompt)
        if "200 dollars" in prompt:
            yield "current corpus answer"
        else:
            yield "legacy corpus answer"


class _FailOnceEmbeddingProvider:
    def __init__(self, delegate: KeywordEmbeddingProvider) -> None:
        self._delegate = delegate
        self._fail_next = True

    @property
    def dimension(self) -> int:
        return self._delegate.dimension

    @property
    def embedding_version(self) -> str:
        return self._delegate.embedding_version

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("embedding write failed")
        return await self._delegate.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        return await self._delegate.embed_query(text)


class _FailOnceVectorStore:
    def __init__(self, delegate: ChromaVectorStore) -> None:
        self._delegate = delegate
        self._fail_next = True

    async def replace_document(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("vector write failed")
        await self._delegate.replace_document(document_id, chunks, embeddings)

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[SearchHit]:
        return await self._delegate.search(query_embedding, limit=limit)


def _build_ingestion(
    runtime: RuntimeFixture,
    *,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> IngestionService:
    return IngestionService(
        runtime.metadata,
        vectors or runtime.vectors,
        embeddings or runtime.embeddings,
        chunk_size=300,
        chunk_overlap=50,
        max_upload_bytes=1_000_000,
    )


async def _find_cache(runtime: RuntimeFixture, question: str):
    return await runtime.metadata.find_cached_answer(
        normalized_question=normalize_question(question),
        question_embedding=await runtime.embeddings.embed_query(question),
        similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
    )


def _document_state(test_data_dir: Path, filename: str) -> dict[str, object] | None:
    with sqlite3.connect(test_data_dir / "metadata.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT content_hash, version
            FROM documents
            WHERE filename = ?
            """,
            (filename,),
        ).fetchone()
    if row is None:
        return None
    return {"content_hash": row[0], "version": row[1]}


async def _vector_state(
    runtime: RuntimeFixture,
    question: str,
) -> list[dict[str, object]]:
    hits = await runtime.vectors.search(
        await runtime.embeddings.embed_query(question),
        limit=5,
    )
    return [
        {
            "filename": hit.chunk.filename,
            "version": hit.chunk.document_version,
            "quote": hit.chunk.text,
        }
        for hit in hits
    ]


def _answer_state(answer) -> dict[str, object]:
    return {
        "cache_hit": answer.cache_hit,
        "cache_match": answer.cache_match,
        "answer": answer.answer,
        "citations": [
            {
                "filename": citation.filename,
                "version": citation.document_version,
                "quote": citation.quote,
            }
            for citation in answer.citations
        ],
    }


async def _seed_related_and_unrelated_cache(runtime: RuntimeFixture) -> None:
    await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=_ORIGINAL_CONTENT,
    )
    await runtime.services.ingestion.ingest(
        filename="deployment.txt",
        content_type="text/plain",
        content=b"Deployment requires two reviewers.",
    )

    related = await runtime.services.questions.answer(_RELATED_QUESTION)
    unrelated = await runtime.services.questions.answer(_UNRELATED_QUESTION)

    assert related.cache_hit is False
    assert unrelated.cache_hit is False
    assert await _find_cache(runtime, _RELATED_QUESTION) is not None
    assert await _find_cache(runtime, _SIMILAR_RELATED_QUESTION) is not None
    assert await _find_cache(runtime, _UNRELATED_QUESTION) is not None


async def _assert_failed_update_is_cache_safe(
    runtime: RuntimeFixture,
    failing_ingestion: IngestionService,
    *,
    expected_error: str,
) -> None:
    await _seed_related_and_unrelated_cache(runtime)

    with pytest.raises(RuntimeError, match=expected_error):
        await failing_ingestion.ingest(
            filename="benefits.txt",
            content_type="text/plain",
            content=_UPDATED_CONTENT,
        )

    assert await _find_cache(runtime, _RELATED_QUESTION) is None
    assert await _find_cache(runtime, _SIMILAR_RELATED_QUESTION) is None
    assert await _find_cache(runtime, _UNRELATED_QUESTION) is not None

    related_during_failure = await runtime.services.questions.answer(_RELATED_QUESTION)
    unrelated_during_failure = await runtime.services.questions.answer(_UNRELATED_QUESTION)
    assert related_during_failure.cache_hit is False
    assert related_during_failure.citations[0].document_version == 1
    assert unrelated_during_failure.cache_hit is True

    recovered = await failing_ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=_UPDATED_CONTENT,
    )
    assert recovered.change is DocumentChange.UNCHANGED
    assert recovered.document.version == 2
    assert recovered.document.content_hash == hashlib.sha256(_UPDATED_CONTENT).hexdigest()
    assert recovered.invalidated_cache_entries == 2

    related_after_retry = await runtime.services.questions.answer(_RELATED_QUESTION)
    unrelated_after_retry = await runtime.services.questions.answer(_UNRELATED_QUESTION)
    assert related_after_retry.cache_hit is False
    assert related_after_retry.citations[0].document_version == 2
    assert "200 dollars" in related_after_retry.citations[0].quote
    assert unrelated_after_retry.cache_hit is False


async def _assert_failed_create_retry_invalidates_corpus_cache(
    test_data_dir: Path,
    runtime: RuntimeFixture,
    failing_ingestion: IngestionService,
    llm: _CorpusAwareLlmGateway,
    *,
    expected_error: str,
) -> None:
    await runtime.services.ingestion.ingest(
        filename="legacy-policy.txt",
        content_type="text/plain",
        content=_LEGACY_CORPUS_CONTENT,
    )
    preexisting_first = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )
    preexisting_second = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )
    preexisting_similar = await _find_cache(
        runtime,
        _SIMILAR_CREATED_FAILURE_QUESTION,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        await failing_ingestion.ingest(
            filename="current-policy.txt",
            content_type="text/plain",
            content=_CURRENT_CORPUS_CONTENT,
        )

    metadata_after_failure = _document_state(test_data_dir, "current-policy.txt")
    vectors_after_failure = await _vector_state(runtime, _CREATED_FAILURE_QUESTION)

    failure_window_first = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )
    failure_window_second = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )

    recovered = await failing_ingestion.ingest(
        filename="current-policy.txt",
        content_type="text/plain",
        content=_CURRENT_CORPUS_CONTENT,
    )
    vectors_after_retry = await _vector_state(runtime, _CREATED_FAILURE_QUESTION)
    after_retry_first = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )
    after_retry_second = await runtime.services.questions.answer(
        _CREATED_FAILURE_QUESTION
    )

    observed = {
        "preexisting": {
            "first": _answer_state(preexisting_first),
            "second_cache_hit": preexisting_second.cache_hit,
            "second_cache_match": preexisting_second.cache_match,
            "similar_cache_exists": preexisting_similar is not None,
        },
        "metadata_after_failure": metadata_after_failure,
        "vectors_after_failure": vectors_after_failure,
        "failure_window": {
            "first": _answer_state(failure_window_first),
            "second_cache_hit": failure_window_second.cache_hit,
            "second_cache_match": failure_window_second.cache_match,
        },
        "retry": {
            "change": recovered.change.value,
            "version": recovered.document.version,
            "content_hash": recovered.document.content_hash,
            "invalidated": recovered.invalidated_cache_entries,
            "vector_filenames": sorted(
                state["filename"] for state in vectors_after_retry
            ),
            "top_vector": vectors_after_retry[0],
        },
        "after_retry": {
            "first": _answer_state(after_retry_first),
            "second_cache_hit": after_retry_second.cache_hit,
            "second_cache_match": after_retry_second.cache_match,
        },
        "llm_calls": len(llm.prompts),
    }
    expected = {
        "preexisting": {
            "first": {
                "cache_hit": False,
                "cache_match": None,
                "answer": "legacy corpus answer",
                "citations": [
                    {
                        "filename": "legacy-policy.txt",
                        "version": 1,
                        "quote": _LEGACY_CORPUS_CONTENT.decode(),
                    }
                ],
            },
            "second_cache_hit": True,
            "second_cache_match": "exact",
            "similar_cache_exists": True,
        },
        "metadata_after_failure": {
            "content_hash": hashlib.sha256(_CURRENT_CORPUS_CONTENT).hexdigest(),
            "version": 1,
        },
        "vectors_after_failure": [
            {
                "filename": "legacy-policy.txt",
                "version": 1,
                "quote": _LEGACY_CORPUS_CONTENT.decode(),
            }
        ],
        "failure_window": {
            "first": {
                "cache_hit": False,
                "cache_match": None,
                "answer": "legacy corpus answer",
                "citations": [
                    {
                        "filename": "legacy-policy.txt",
                        "version": 1,
                        "quote": _LEGACY_CORPUS_CONTENT.decode(),
                    }
                ],
            },
            "second_cache_hit": True,
            "second_cache_match": "exact",
        },
        "retry": {
            "change": "unchanged",
            "version": 1,
            "content_hash": hashlib.sha256(_CURRENT_CORPUS_CONTENT).hexdigest(),
            "invalidated": 1,
            "vector_filenames": ["current-policy.txt", "legacy-policy.txt"],
            "top_vector": {
                "filename": "current-policy.txt",
                "version": 1,
                "quote": _CURRENT_CORPUS_CONTENT.decode(),
            },
        },
        "after_retry": {
            "first": {
                "cache_hit": False,
                "cache_match": None,
                "answer": "current corpus answer",
                "citations": [
                    {
                        "filename": "current-policy.txt",
                        "version": 1,
                        "quote": _CURRENT_CORPUS_CONTENT.decode(),
                    },
                    {
                        "filename": "legacy-policy.txt",
                        "version": 1,
                        "quote": _LEGACY_CORPUS_CONTENT.decode(),
                    },
                ],
            },
            "second_cache_hit": True,
            "second_cache_match": "exact",
        },
        "llm_calls": 3,
    }
    assert observed == expected


async def test_embedding_failure_invalidates_related_cache_before_retry(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir, llm=FakeLlmGateway())
    failing_ingestion = _build_ingestion(
        runtime,
        embeddings=_FailOnceEmbeddingProvider(runtime.embeddings),
    )

    await _assert_failed_update_is_cache_safe(
        runtime,
        failing_ingestion,
        expected_error="embedding write failed",
    )


async def test_vector_failure_invalidates_related_cache_before_retry(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir, llm=FakeLlmGateway())
    failing_ingestion = _build_ingestion(
        runtime,
        vectors=_FailOnceVectorStore(runtime.vectors),
    )

    await _assert_failed_update_is_cache_safe(
        runtime,
        failing_ingestion,
        expected_error="vector write failed",
    )


async def test_created_embedding_failure_retry_invalidates_failure_window_recache(
    test_data_dir,
) -> None:
    llm = _CorpusAwareLlmGateway()
    runtime = await build_test_runtime(
        test_data_dir,
        llm=llm,
        vocabulary=("policy", "legacy", "current"),
        minimum_relevance_score=0.5,
    )
    failing_ingestion = _build_ingestion(
        runtime,
        embeddings=_FailOnceEmbeddingProvider(runtime.embeddings),
    )

    await _assert_failed_create_retry_invalidates_corpus_cache(
        test_data_dir,
        runtime,
        failing_ingestion,
        llm,
        expected_error="embedding write failed",
    )


async def test_created_vector_failure_retry_invalidates_failure_window_recache(
    test_data_dir,
) -> None:
    llm = _CorpusAwareLlmGateway()
    runtime = await build_test_runtime(
        test_data_dir,
        llm=llm,
        vocabulary=("policy", "legacy", "current"),
        minimum_relevance_score=0.5,
    )
    failing_ingestion = _build_ingestion(
        runtime,
        vectors=_FailOnceVectorStore(runtime.vectors),
    )

    await _assert_failed_create_retry_invalidates_corpus_cache(
        test_data_dir,
        runtime,
        failing_ingestion,
        llm,
        expected_error="vector write failed",
    )


async def test_normal_same_hash_reupload_invalidates_corpus_cache(
    test_data_dir,
) -> None:
    llm = FakeLlmGateway()
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    await _seed_related_and_unrelated_cache(runtime)
    calls_before_reupload = len(llm.prompts)

    reuploaded = await runtime.services.ingestion.ingest(
        filename="deployment.txt",
        content_type="text/plain",
        content=b"Deployment requires two reviewers.",
    )
    related_after_reupload = await runtime.services.questions.answer(
        _RELATED_QUESTION
    )
    unrelated_after_reupload = await runtime.services.questions.answer(
        _UNRELATED_QUESTION
    )

    assert reuploaded.change is DocumentChange.UNCHANGED
    assert reuploaded.document.version == 1
    assert reuploaded.invalidated_cache_entries == 2
    assert related_after_reupload.cache_hit is False
    assert related_after_reupload.citations[0].filename == "benefits.txt"
    assert unrelated_after_reupload.cache_hit is False
    assert unrelated_after_reupload.citations[0].filename == "deployment.txt"
    assert len(llm.prompts) == calls_before_reupload + 2


async def test_document_update_invalidates_related_cache_only(test_data_dir) -> None:
    llm = FakeLlmGateway(("grounded ", "answer"))
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    first = await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=b"Education allowance is 100 dollars.",
    )
    await runtime.services.ingestion.ingest(
        filename="deployment.txt",
        content_type="text/plain",
        content=b"Deployment requires two reviewers.",
    )

    first_answer = await runtime.services.questions.answer(
        "Explain the education allowance"
    )
    second_answer = await runtime.services.questions.answer(
        "Explain the deployment rule"
    )
    similar_answer = await runtime.services.questions.answer(
        "Education details, please"
    )
    calls_before_update = len(llm.prompts)

    updated = await runtime.services.ingestion.ingest(
        filename="benefits.txt",
        content_type="text/plain",
        content=b"Education allowance is now 200 dollars.",
    )
    unrelated_after_update = await runtime.services.questions.answer(
        "Explain the deployment rule"
    )
    related_after_update = await runtime.services.questions.answer(
        "Explain the education allowance"
    )

    assert first.change.value == "created"
    assert first_answer.cache_hit is False
    assert second_answer.cache_hit is False
    assert similar_answer.cache_hit is True
    assert similar_answer.cache_match == "similar"
    assert calls_before_update == 2
    assert updated.change.value == "updated"
    assert updated.document.version == 2
    assert updated.invalidated_cache_entries == 1
    assert unrelated_after_update.cache_hit is True
    assert related_after_update.cache_hit is False
    assert len(llm.prompts) == calls_before_update + 1
