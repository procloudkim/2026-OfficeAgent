"""Unit tests for format support and source provenance."""

from __future__ import annotations

from datetime import UTC, datetime

from officeagent.application.chunking import chunk_sections
from officeagent.domain.models import DocumentRecord
from officeagent.infrastructure.parsers import extract_sections
from tests.helpers import make_text_pdf


def test_txt_and_pdf_extraction_preserves_format_specific_location() -> None:
    text_sections = extract_sections("policy.txt", "교육비 한도는 200만원".encode())
    pdf_sections = extract_sections(
        "guide.pdf",
        make_text_pdf("Deployment requires two reviewers."),
    )

    assert text_sections[0].page_number is None
    assert "교육비" in text_sections[0].text
    assert pdf_sections[0].page_number == 1
    assert "two reviewers" in pdf_sections[0].text


def test_chunking_keeps_offsets_and_stable_identifiers() -> None:
    section = extract_sections("policy.txt", ("alpha beta gamma " * 60).encode())
    now = datetime.now(UTC)
    document = DocumentRecord(
        id="doc-1",
        filename="policy.txt",
        content_type="text/plain",
        content_hash="hash",
        version=1,
        created_at=now,
        updated_at=now,
    )

    first = chunk_sections(document, section, chunk_size=200, overlap=40)
    second = chunk_sections(document, section, chunk_size=200, overlap=40)

    assert len(first) > 1
    assert first == second
    assert all(chunk.start_offset < chunk.end_offset for chunk in first)
    assert first[0].filename == "policy.txt"
