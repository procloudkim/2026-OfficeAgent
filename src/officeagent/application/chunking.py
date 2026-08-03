"""Deterministic, provenance-preserving text chunking."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from officeagent.domain.models import Chunk, DocumentRecord, ExtractedSection, PreparedChunk

CHUNKING_ALGORITHM_VERSION = "page-safe-dual-bound-v1"
LEGACY_CHARACTER_CHUNKING_VERSION = "page-safe-character-v1"


class ChunkTokenLimiter(Protocol):
    """Return a valid Unicode code-point prefix bounded by a tokenizer proxy."""

    @property
    def token_counter_version(self) -> str: ...

    def bounded_prefix(self, text: str, *, max_tokens: int) -> str: ...


class ChunkTokenizationError(RuntimeError):
    """A sanitized tokenizer failure that must not be classified as user input."""


def build_chunking_version(
    *,
    chunk_size: int,
    overlap: int,
    token_counter_version: str,
) -> str:
    """Fingerprint every input that changes transactional chunk boundaries."""

    _validate_window(chunk_size, overlap)
    if not token_counter_version.strip():
        raise ValueError("token_counter_version must not be blank")
    payload = (
        f"algorithm={CHUNKING_ALGORITHM_VERSION}\n"
        f"max_characters={chunk_size}\n"
        f"max_tokens={chunk_size}\n"
        f"overlap_characters={overlap}\n"
        f"token_counter={token_counter_version}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{CHUNKING_ALGORITHM_VERSION}:{digest}"


def prepare_chunks(
    sections: Sequence[ExtractedSection],
    *,
    chunk_size: int,
    overlap: int,
    token_limiter: ChunkTokenLimiter | None = None,
) -> tuple[PreparedChunk, ...]:
    """Create page-safe chunks bounded by characters and, when supplied, tokens."""

    _validate_window(chunk_size, overlap)
    chunks: list[PreparedChunk] = []
    chunk_index = 0
    for section in sections:
        cursor = 0
        text = section.text
        while cursor < len(text):
            end = _bounded_end(
                text,
                cursor,
                chunk_size=chunk_size,
                token_limiter=token_limiter,
            )
            raw = text[cursor:end]
            left_trimmed = len(raw) - len(raw.lstrip())
            right_trimmed = len(raw.rstrip())
            cleaned = raw.strip()
            if cleaned:
                chunks.append(
                    PreparedChunk(
                        text=cleaned,
                        chunk_index=chunk_index,
                        page_number=section.page_number,
                        start_offset=section.start_offset + cursor + left_trimmed,
                        end_offset=section.start_offset + cursor + right_trimmed,
                    )
                )
                chunk_index += 1

            if end >= len(text):
                break
            next_cursor = end - overlap
            cursor = next_cursor if next_cursor > cursor else end
    return tuple(chunks)


def chunk_sections(
    document: DocumentRecord,
    sections: Sequence[ExtractedSection],
    *,
    chunk_size: int,
    overlap: int,
    token_limiter: ChunkTokenLimiter | None = None,
) -> list[Chunk]:
    """Split sections without crossing page boundaries."""

    prepared = prepare_chunks(
        sections,
        chunk_size=chunk_size,
        overlap=overlap,
        token_limiter=token_limiter,
    )
    chunks: list[Chunk] = []
    for chunk in prepared:
        identity = (
            f"{document.id}:{document.version}:{chunk.chunk_index}:"
            f"{chunk.start_offset}:{chunk.end_offset}:{chunk.text}"
        )
        chunks.append(
            Chunk(
                id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
                document_id=document.id,
                document_version=document.version,
                filename=document.filename,
                text=chunk.text,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
        )
    return chunks


def _validate_window(chunk_size: int, overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")


def _bounded_end(
    text: str,
    start: int,
    *,
    chunk_size: int,
    token_limiter: ChunkTokenLimiter | None,
) -> int:
    proposed_end = min(start + chunk_size, len(text))
    character_end = _prefer_whitespace_boundary(text, start, proposed_end, chunk_size)
    if token_limiter is None:
        return character_end

    raw_candidate = text[start:character_end]
    left_trimmed = len(raw_candidate) - len(raw_candidate.lstrip())
    candidate = raw_candidate.strip()
    if not candidate:
        return character_end

    bounded = _safe_token_prefix(
        candidate,
        token_limiter=token_limiter,
        max_tokens=chunk_size,
    )
    if bounded == candidate:
        return character_end
    return start + left_trimmed + len(bounded)


def _safe_token_prefix(
    text: str,
    *,
    token_limiter: ChunkTokenLimiter,
    max_tokens: int,
) -> str:
    """Bound already-trimmed text and recheck after trailing-space removal.

    BPE token counts are context-sensitive: removing a leading or trailing space
    can increase the token count.  The stored chunk is stripped, so the limiter
    must see and approve that exact string before offsets are committed.
    """

    candidate = text
    try:
        while candidate:
            bounded = token_limiter.bounded_prefix(candidate, max_tokens=max_tokens)
            if not bounded or not candidate.startswith(bounded):
                raise ChunkTokenizationError("document chunk tokenization failed")
            normalized = bounded.rstrip()
            if not normalized:
                raise ChunkTokenizationError("document chunk tokenization failed")
            verified = token_limiter.bounded_prefix(
                normalized,
                max_tokens=max_tokens,
            )
            if verified == normalized:
                return normalized
            if not verified or not normalized.startswith(verified):
                raise ChunkTokenizationError("document chunk tokenization failed")
            candidate = verified.rstrip()
    except Exception as error:
        if isinstance(error, ChunkTokenizationError):
            raise
        raise ChunkTokenizationError("document chunk tokenization failed") from error
    raise ChunkTokenizationError("document chunk tokenization failed")


def _prefer_whitespace_boundary(
    text: str,
    start: int,
    proposed_end: int,
    chunk_size: int,
) -> int:
    if proposed_end >= len(text):
        return len(text)

    minimum = start + int(chunk_size * 0.6)
    boundary = text.rfind(" ", minimum, proposed_end)
    newline = text.rfind("\n", minimum, proposed_end)
    return max(boundary, newline) + 1 if max(boundary, newline) >= minimum else proposed_end
