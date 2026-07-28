"""Document ingestion use case."""

from __future__ import annotations

import hashlib

import anyio

from officeagent.application.chunking import chunk_sections
from officeagent.domain.models import DocumentChange, IngestionResult
from officeagent.domain.ports import EmbeddingProvider, MetadataStore, VectorStore
from officeagent.infrastructure.parsers import extract_sections, normalize_filename


class DocumentTooLargeError(ValueError):
    """Raised before parsing when an upload exceeds the configured bound."""


class IngestionService:
    """Extract, version, chunk, embed, index, and invalidate as one use case."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        *,
        chunk_size: int,
        chunk_overlap: int,
        max_upload_bytes: int,
    ) -> None:
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_upload_bytes = max_upload_bytes

    @property
    def max_upload_bytes(self) -> int:
        """Expose the transport read bound without leaking other internals."""

        return self._max_upload_bytes

    async def ingest(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> IngestionResult:
        """Persist one file and expose whether it changed the corpus."""

        safe_filename = normalize_filename(filename)
        if len(content) > self._max_upload_bytes:
            raise DocumentTooLargeError(
                f"Upload exceeds the {self._max_upload_bytes}-byte limit"
            )
        sections = await anyio.to_thread.run_sync(extract_sections, safe_filename, content)
        content_hash = hashlib.sha256(content).hexdigest()
        upsert = await self._metadata_store.upsert_document(
            filename=safe_filename,
            content_type=content_type or "application/octet-stream",
            content_hash=content_hash,
        )
        chunks = chunk_sections(
            upsert.document,
            sections,
            chunk_size=self._chunk_size,
            overlap=self._chunk_overlap,
        )
        if not chunks:
            raise ValueError("The document does not contain searchable text")
        embeddings = await self._embedding_provider.embed_documents(
            [chunk.text for chunk in chunks]
        )

        # Re-index unchanged uploads as well. This makes a retry repair a prior
        # vector-write failure without falsely advancing the document version.
        await self._vector_store.replace_document(
            upsert.document.id,
            chunks,
            embeddings,
        )

        invalidated = 0
        if upsert.change is DocumentChange.CREATED:
            # A larger corpus may change the best evidence for every question.
            invalidated = await self._metadata_store.invalidate_all_cache()
        elif upsert.change is DocumentChange.UPDATED:
            invalidated = await self._metadata_store.invalidate_cache_for_document(
                upsert.document.id
            )

        return IngestionResult(
            document=upsert.document,
            change=upsert.change,
            chunk_count=len(chunks),
            invalidated_cache_entries=invalidated,
        )
