"""Unit tests for format support and source provenance."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from officeagent.application.chunking import (
    ChunkTokenizationError,
    build_chunking_version,
    chunk_sections,
    prepare_chunks,
)
from officeagent.application.question_answering import build_retrieval_version
from officeagent.domain.indexing import build_indexing_version
from officeagent.domain.models import DocumentRecord, ExtractedSection
from officeagent.infrastructure.parsers import extract_sections
from officeagent.infrastructure.tokenization import TiktokenTokenCounter
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


def test_dual_bound_chunking_preserves_code_point_offsets_and_token_ceiling() -> None:
    tokenizer = TiktokenTokenCounter()
    sections = (
        ExtractedSection(
            text="🧑🏽‍💻資料보안정책 " * 80,
            page_number=1,
            start_offset=0,
            end_offset=len("🧑🏽‍💻資料보안정책 " * 80),
        ),
        ExtractedSection(
            text="두 번째 페이지는 독립적으로 시작합니다.",
            page_number=2,
            start_offset=0,
            end_offset=len("두 번째 페이지는 독립적으로 시작합니다."),
        ),
    )

    chunks = prepare_chunks(
        sections,
        chunk_size=80,
        overlap=10,
        token_limiter=tokenizer,
    )

    assert len(chunks) > 2
    assert {chunk.page_number for chunk in chunks} == {1, 2}
    for chunk in chunks:
        source = sections[chunk.page_number - 1].text
        assert chunk.text == source[chunk.start_offset : chunk.end_offset]
        assert len(chunk.text) <= 80
        assert tokenizer.count_tokens_sync(chunk.text) <= 80


def test_trimmed_chunk_is_rechecked_against_the_token_ceiling() -> None:
    tokenizer = TiktokenTokenCounter()
    text = " PostgreSQL" + "🧑" * 66
    assert tokenizer.count_tokens_sync(text) == 200
    assert tokenizer.count_tokens_sync(text.strip()) == 201
    sections = (
        ExtractedSection(
            text=text,
            page_number=1,
            start_offset=0,
            end_offset=len(text),
        ),
    )

    chunks = prepare_chunks(
        sections,
        chunk_size=200,
        overlap=0,
        token_limiter=tokenizer,
    )

    assert chunks
    for chunk in chunks:
        assert chunk.text == text[chunk.start_offset : chunk.end_offset]
        assert tokenizer.count_tokens_sync(chunk.text) <= 200


def test_token_ceiling_does_not_change_existing_safe_character_boundaries() -> None:
    tokenizer = TiktokenTokenCounter()
    sections = extract_sections("policy.txt", ("alpha beta gamma " * 60).encode())
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

    legacy = chunk_sections(document, sections, chunk_size=200, overlap=40)
    bounded = chunk_sections(
        document,
        sections,
        chunk_size=200,
        overlap=40,
        token_limiter=tokenizer,
    )

    assert bounded == legacy


def test_chunking_version_covers_policy_without_exposing_tokenizer_identity() -> None:
    tokenizer_identity = "private/tokenizer-path:o200k_base"
    baseline = build_chunking_version(
        chunk_size=900,
        overlap=150,
        token_counter_version=tokenizer_identity,
    )

    assert baseline.startswith("page-safe-dual-bound-v1:")
    assert tokenizer_identity not in baseline
    assert baseline != build_chunking_version(
        chunk_size=901,
        overlap=150,
        token_counter_version=tokenizer_identity,
    )
    assert baseline != build_chunking_version(
        chunk_size=900,
        overlap=149,
        token_counter_version=tokenizer_identity,
    )
    assert baseline != build_chunking_version(
        chunk_size=900,
        overlap=150,
        token_counter_version="next-tokenizer",
    )

    retrieval = build_retrieval_version(
        limit=5,
        minimum_score=0.3,
        cache_threshold=0.95,
        chunking_version=baseline,
    )
    assert baseline not in retrieval
    assert retrieval != build_retrieval_version(
        limit=5,
        minimum_score=0.3,
        cache_threshold=0.95,
        chunking_version="different-policy",
    )
    assert build_retrieval_version(
        limit=5,
        minimum_score=0.3,
        cache_threshold=0.95,
    ).startswith("cosine-score-v1:")


def test_indexing_version_covers_chunks_and_embeddings_without_exposing_identity() -> None:
    chunking_version = "page-safe-dual-bound-v1:chunk-private"
    embedding_version = "fastembed:/private/model/path:dimension=384"
    baseline = build_indexing_version(
        chunking_version=chunking_version,
        embedding_version=embedding_version,
    )

    assert baseline.startswith("document-index-v1:")
    assert chunking_version not in baseline
    assert embedding_version not in baseline
    assert baseline != build_indexing_version(
        chunking_version="different-chunks",
        embedding_version=embedding_version,
    )
    assert baseline != build_indexing_version(
        chunking_version=chunking_version,
        embedding_version="different-embedding",
    )


def test_chunk_tokenizer_failure_is_sanitized_and_fails_closed() -> None:
    class FailingLimiter:
        @property
        def token_counter_version(self) -> str:
            return "failing-test-v1"

        def bounded_prefix(self, text: str, *, max_tokens: int) -> str:
            del text, max_tokens
            raise ValueError("private/tokenizer/cache/path")

    sections = (
        ExtractedSection(text="searchable", page_number=None, start_offset=0, end_offset=10),
    )

    with pytest.raises(ChunkTokenizationError) as captured:
        prepare_chunks(
            sections,
            chunk_size=8,
            overlap=1,
            token_limiter=FailingLimiter(),
        )

    assert "private" not in str(captured.value)
