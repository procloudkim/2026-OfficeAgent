"""Prepare document work before one transactional corpus commit."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from functools import partial
from typing import Protocol

import anyio

from officeagent.application.chunking import (
    ChunkTokenLimiter,
    build_chunking_version,
    prepare_chunks,
)
from officeagent.application.ingestion import DocumentTooLargeError
from officeagent.application.observability import (
    PipelineOperation,
    PipelineOutcome,
    PipelineTimer,
    TimingRecorder,
    TimingStage,
    outcome_for_exception,
)
from officeagent.domain.models import (
    CacheVersions,
    IngestionResult,
    PreparedChunk,
)
from officeagent.domain.ports import EmbeddingProvider
from officeagent.infrastructure.parsers import (
    EmptyDocumentError,
    extract_sections,
    normalize_filename,
)


class CorpusWriter(Protocol):
    """Duck-typed transaction boundary."""

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
    ) -> IngestionResult: ...

class TransactionalIngestionService:
    """Finish parser/chunk/embed work before exactly one writer call."""

    def __init__(
        self,
        corpus_writer: CorpusWriter,
        embedding_provider: EmbeddingProvider,
        *,
        chunk_size: int,
        chunk_overlap: int,
        token_limiter: ChunkTokenLimiter,
        max_upload_bytes: int,
        cache_versions: CacheVersions,
        retrieval_threshold: float,
        timing_recorder: TimingRecorder | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        if cache_versions.embedding_version != embedding_provider.embedding_version:
            raise ValueError("cache and ingestion embedding versions must match")
        self._corpus_writer = corpus_writer
        self._embedding_provider = embedding_provider
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._token_limiter = token_limiter
        self._chunking_version = build_chunking_version(
            chunk_size=chunk_size,
            overlap=chunk_overlap,
            token_counter_version=token_limiter.token_counter_version,
        )
        self._max_upload_bytes = max_upload_bytes
        self._cache_versions = cache_versions
        self._retrieval_threshold = retrieval_threshold
        self._timing_recorder = timing_recorder
        self._clock = clock

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    async def ingest(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        request_id: str | None = None,
    ) -> IngestionResult:
        """Do not reach the DB boundary unless every preparation step succeeds."""

        timer = PipelineTimer(
            PipelineOperation.DOCUMENT_INGESTION,
            request_id=request_id,
            recorder=self._timing_recorder,
            clock=self._clock,
        )
        try:
            if len(content) > self._max_upload_bytes:
                raise DocumentTooLargeError(
                    f"Upload exceeds the {self._max_upload_bytes}-byte limit"
                )
            safe_filename = normalize_filename(filename)
            with timer.measure(TimingStage.PARSE):
                sections = await anyio.to_thread.run_sync(
                    extract_sections,
                    safe_filename,
                    content,
                )
            with timer.measure(TimingStage.PARSE):
                chunks = await anyio.to_thread.run_sync(
                    partial(
                        prepare_chunks,
                        sections,
                        chunk_size=self._chunk_size,
                        overlap=self._chunk_overlap,
                        token_limiter=self._token_limiter,
                    )
                )
            if not chunks:
                raise EmptyDocumentError("The document does not contain searchable text")
            with timer.measure(TimingStage.EMBED):
                embeddings = await self._embedding_provider.embed_documents(
                    [chunk.text for chunk in chunks]
                )
            if len(embeddings) != len(chunks):
                raise ValueError(
                    "Embedding provider returned a vector count different from chunks"
                )
            with timer.measure(TimingStage.PERSIST):
                result = await self._corpus_writer.commit_document(
                    filename=safe_filename,
                    content_type=content_type or "application/octet-stream",
                    content_hash=hashlib.sha256(content).hexdigest(),
                    chunking_version=self._chunking_version,
                    chunks=chunks,
                    embeddings=embeddings,
                    cache_versions=self._cache_versions,
                    retrieval_threshold=self._retrieval_threshold,
                )
        except BaseException as exc:
            timer.finish(
                outcome_for_exception(exc),
                error_type=type(exc).__name__,
            )
            raise
        timer.finish(PipelineOutcome.COMPLETED)
        return result
