"""Ports owned by the application core."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from officeagent.domain.models import (
    CachedAnswer,
    CacheVersions,
    CacheWriteResult,
    Chunk,
    Citation,
    DocumentRecord,
    DocumentUpsertResult,
    ReadinessStatus,
    RetrievalSnapshot,
    SearchHit,
)


class EmbeddingProvider(Protocol):
    """Create vectors at the document and query grains."""

    @property
    def dimension(self) -> int:
        """Return the fixed embedding dimension."""

    @property
    def embedding_version(self) -> str:
        """Return the semantic version of the embedding computation."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed document passages."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query."""


class PromptTokenCounter(Protocol):
    """Count one rendered prompt with a named, deterministic tokenizer proxy."""

    @property
    def token_counter_version(self) -> str:
        """Identify the tokenizer whose counts define the prompt budget."""

    async def count_tokens(self, text: str) -> int:
        """Return the token count for one complete rendered prompt."""


class ReadinessProbe(Protocol):
    """Verify product dependencies without invoking the LLM."""

    async def check(self) -> ReadinessStatus:
        """Return credential-free readiness facts or raise on failure."""


class VectorStore(Protocol):
    """Persist and retrieve document chunks through vector similarity."""

    async def replace_document(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Replace a document's chunks on success.

        Adapter-level and cross-store atomicity are not guaranteed.
        """

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
        cache_versions: CacheVersions,
    ) -> CachedAnswer | None:
        """Find an exact cache entry first, then a sufficiently similar one."""

    async def save_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        answer: str,
        citations: Sequence[Citation],
        cache_versions: CacheVersions,
    ) -> str:
        """Persist an answer and its document dependencies."""

    async def invalidate_cache_for_document(self, document_id: str) -> int:
        """Invalidate entries whose citations depend on one updated document."""

    async def invalidate_all_cache(self) -> int:
        """Invalidate all entries after corpus expansion."""


class RevisionAwareCorpusStore(Protocol):
    """Cache and retrieval port backed by one revisioned corpus transaction model."""

    async def find_cached_answer(
        self,
        *,
        normalized_question: str,
        question_embedding: Sequence[float],
        similarity_threshold: float,
        cache_versions: CacheVersions,
    ) -> CachedAnswer | None:
        """Return a current-version exact or similar cache entry."""

    async def retrieve(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> RetrievalSnapshot:
        """Return evidence and the revision observed with it."""

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
        """Write only if the current corpus revision still matches."""


class LlmGateway(Protocol):
    """Stream an answer from the selected subscription-backed LLM SDK."""

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        """Yield real answer deltas as the underlying runtime emits them."""
