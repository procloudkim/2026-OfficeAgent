"""Deterministic, provenance-preserving text chunking."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from officeagent.domain.models import Chunk, DocumentRecord, ExtractedSection


def chunk_sections(
    document: DocumentRecord,
    sections: Sequence[ExtractedSection],
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """Split sections without crossing page boundaries."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")

    chunks: list[Chunk] = []
    chunk_index = 0
    for section in sections:
        cursor = 0
        text = section.text
        while cursor < len(text):
            proposed_end = min(cursor + chunk_size, len(text))
            end = _prefer_whitespace_boundary(text, cursor, proposed_end, chunk_size)
            raw = text[cursor:end]
            left_trimmed = len(raw) - len(raw.lstrip())
            right_trimmed = len(raw.rstrip())
            cleaned = raw.strip()
            if cleaned:
                start_offset = section.start_offset + cursor + left_trimmed
                end_offset = section.start_offset + cursor + right_trimmed
                identity = (
                    f"{document.id}:{document.version}:{chunk_index}:"
                    f"{start_offset}:{end_offset}:{cleaned}"
                )
                chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        document_id=document.id,
                        document_version=document.version,
                        filename=document.filename,
                        text=cleaned,
                        chunk_index=chunk_index,
                        page_number=section.page_number,
                        start_offset=start_offset,
                        end_offset=end_offset,
                    )
                )
                chunk_index += 1

            if end >= len(text):
                break
            next_cursor = end - overlap
            cursor = next_cursor if next_cursor > cursor else end

    return chunks


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
