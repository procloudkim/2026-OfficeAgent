"""Framework-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DocumentChange(StrEnum):
    """Result of comparing an upload with the current document version."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    """A provenance-preserving section extracted from one source file."""

    text: str
    page_number: int | None
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Current persisted state of an uploaded document."""

    id: str
    filename: str
    content_type: str
    content_hash: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentUpsertResult:
    """Document record together with the detected change kind."""

    document: DocumentRecord
    change: DocumentChange


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """Document-independent chunk prepared before the database transaction."""

    text: str
    chunk_index: int
    page_number: int | None
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class Chunk:
    """Searchable text unit with enough metadata to build a citation."""

    id: str
    document_id: str
    document_version: int
    filename: str
    text: str
    chunk_index: int
    page_number: int | None
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One vector-search result. Score is normalized so larger is better."""

    chunk: Chunk
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalSnapshot:
    """Retrieved evidence and the corpus revision observed with it."""

    revision: int
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    """Credential-free database readiness facts exposed by the API."""

    migration_head: str
    vector_version: str


@dataclass(frozen=True, slots=True)
class Citation:
    """Stable, reader-facing evidence pointer."""

    document_id: str
    document_version: int
    filename: str
    chunk_id: str
    chunk_index: int
    page_number: int | None
    start_offset: int
    end_offset: int
    quote: str
    score: float


@dataclass(frozen=True, slots=True)
class CacheVersions:
    """Semantic identity of one cached answer computation."""

    prompt_version: str
    embedding_version: str
    retrieval_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("prompt_version", self.prompt_version),
            ("embedding_version", self.embedding_version),
            ("retrieval_version", self.retrieval_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    """Answer cache entry returned by exact or semantic lookup."""

    id: str
    normalized_question: str
    answer: str
    citations: tuple[Citation, ...]
    similarity: float
    versions: CacheVersions


@dataclass(frozen=True, slots=True)
class CacheWriteResult:
    """Outcome of a revision-guarded cache write."""

    written: bool
    cache_id: str | None = None
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Complete answer used by the optional non-streaming endpoint."""

    answer: str
    citations: tuple[Citation, ...]
    cache_hit: bool
    cache_match: str | None
    grounded: bool
    cache_written: bool
    cache_skip_reason: str | None


class AnswerEventType(StrEnum):
    """Stable server-sent event names."""

    STARTED = "answer.started"
    DELTA = "answer.delta"
    CITATION = "answer.citation"
    COMPLETED = "answer.completed"
    ERROR = "answer.error"


@dataclass(frozen=True, slots=True)
class AnswerEvent:
    """Transport-neutral streaming event."""

    type: AnswerEventType
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Observable outcome of ingesting one upload."""

    document: DocumentRecord
    change: DocumentChange
    chunk_count: int
    invalidated_cache_entries: int
