"""Versioned HTTP request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    """Result of a document upload."""

    document_id: str
    filename: str
    content_type: str
    content_hash: str
    document_version: int
    change: str
    chunk_count: int
    invalidated_cache_entries: int
    updated_at: datetime


class QuestionRequest(BaseModel):
    """Question asked against the current document corpus."""

    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4_000)


class CitationResponse(BaseModel):
    """Evidence pointer returned with a grounded answer."""

    model_config = ConfigDict(from_attributes=True)

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


class AnswerResponse(BaseModel):
    """Collected answer for non-streaming clients."""

    request_id: str
    answer: str
    citations: list[CitationResponse]
    cache_hit: bool
    cache_match: str | None
    grounded: bool
