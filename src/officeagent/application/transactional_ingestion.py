"""Prepare document work before one transactional corpus commit."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

import anyio

from officeagent.application.ingestion import DocumentTooLargeError
from officeagent.domain.models import (
    CacheVersions,
    ExtractedSection,
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
        max_upload_bytes: int,
        cache_versions: CacheVersions,
        retrieval_threshold: float,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        self._corpus_writer = corpus_writer
        self._embedding_provider = embedding_provider
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_upload_bytes = max_upload_bytes
        self._cache_versions = cache_versions
        self._retrieval_threshold = retrieval_threshold

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    async def ingest(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> IngestionResult:
        """Do not reach the DB boundary unless every preparation step succeeds."""

        if len(content) > self._max_upload_bytes:
            raise DocumentTooLargeError(
                f"Upload exceeds the {self._max_upload_bytes}-byte limit"
            )
        safe_filename = normalize_filename(filename)
        sections = await anyio.to_thread.run_sync(
            extract_sections,
            safe_filename,
            content,
        )
        chunks = _prepare_chunks(
            sections, chunk_size=self._chunk_size, overlap=self._chunk_overlap
        )
        if not chunks:
            raise EmptyDocumentError("The document does not contain searchable text")
        embeddings = await self._embedding_provider.embed_documents(
            [chunk.text for chunk in chunks]
        )
        if len(embeddings) != len(chunks):
            raise ValueError(
                "Embedding provider returned a vector count different from chunks"
            )
        return await self._corpus_writer.commit_document(
            filename=safe_filename,
            content_type=content_type or "application/octet-stream",
            content_hash=hashlib.sha256(content).hexdigest(),
            chunks=chunks,
            embeddings=embeddings,
            cache_versions=self._cache_versions,
            retrieval_threshold=self._retrieval_threshold,
        )


def _prepare_chunks(
    sections: Sequence[ExtractedSection], *, chunk_size: int, overlap: int
) -> tuple[PreparedChunk, ...]:
    """Use V1-equivalent deterministic page/offset chunk boundaries."""

    chunks: list[PreparedChunk] = []
    chunk_index = 0
    for section in sections:
        cursor = 0
        while cursor < len(section.text):
            proposed_end = min(cursor + chunk_size, len(section.text))
            end = _prefer_whitespace_boundary(section.text, cursor, proposed_end, chunk_size)
            raw = section.text[cursor:end]
            left_trimmed = len(raw) - len(raw.lstrip())
            right_trimmed = len(raw.rstrip())
            text = raw.strip()
            if text:
                chunks.append(
                    PreparedChunk(
                        text=text,
                        chunk_index=chunk_index,
                        page_number=section.page_number,
                        start_offset=section.start_offset + cursor + left_trimmed,
                        end_offset=section.start_offset + cursor + right_trimmed,
                    )
                )
                chunk_index += 1
            if end >= len(section.text):
                break
            cursor = end - overlap if end - overlap > cursor else end
    return tuple(chunks)


def _prefer_whitespace_boundary(
    text: str,
    start: int,
    proposed_end: int,
    chunk_size: int,
) -> int:
    if proposed_end >= len(text):
        return len(text)
    minimum = start + int(chunk_size * 0.6)
    boundary = max(
        text.rfind(" ", minimum, proposed_end),
        text.rfind("\n", minimum, proposed_end),
    )
    return boundary + 1 if boundary >= minimum else proposed_end
