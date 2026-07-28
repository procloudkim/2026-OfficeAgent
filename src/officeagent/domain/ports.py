"""Ports owned by the application core."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from officeagent.domain.models import (
    CachedAnswer,
    Chunk,
    Citation,
    DocumentRecord,
    DocumentUpsertResult,
    SearchHit,
)


class EmbeddingProvider(Protocol):
    """Create vectors at the document and query grains."""

    @property
    def dimension(self) -> int:
        """Return the fixed embedding dimension."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed document passages."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query."""


class VectorStore(Protocol):
    """Persist and retrieve document chunks through vector similarity."""

    async def replace_document(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Atomically replace every indexed version of a document."""

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[SearchHit]:
        """Return the most relevant chunks, best match first."""


class MetadataStore(Protocol):
    """Persist documents and answer-cache metadata."""

    async def initialize(self) -> None:
        """Create required schema idempotently."""

    async def upsert_document(
        self,
        *,
        filename: str,
        content_type: str,
        content_hash: str,
    ) -> DocumentUpsertResult:
        """Create, update, or recognize an unchanged document."""

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        """Return the current document state."""

    async def find_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        similarity_threshold: float,
    ) -> CachedAnswer | None:
        """Find an exact cache entry first, then a sufficiently similar one."""

    async def save_cached_answer(
        self,
        *,
        question: str,
        normalized_question: str,
        question_embedding: Sequence[float],
        answer: str,
        citations: Sequence[Citation],
    ) -> str:
        """Persist an answer and its document dependencies."""

    async def invalidate_cache_for_document(self, document_id: str) -> int:
        """Invalidate entries whose citations depend on one updated document."""

    async def invalidate_all_cache(self) -> int:
        """Invalidate all entries after corpus expansion."""


class LlmGateway(Protocol):
    """Stream an answer from the selected subscription-backed LLM SDK."""

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        """Yield real answer deltas as the underlying runtime emits them."""
