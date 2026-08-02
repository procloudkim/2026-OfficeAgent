"""Offline V2 paths with only the LLM and embedding model replaced by doubles."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from officeagent.api.app import ServiceContainer, create_app
from officeagent.application.prompts import PROMPT_TEMPLATE_VERSION
from officeagent.application.question_answering import (
    NO_EVIDENCE_ANSWER,
    QuestionAnsweringService,
    build_retrieval_version,
)
from officeagent.application.transactional_ingestion import TransactionalIngestionService
from officeagent.domain.models import CacheVersions
from officeagent.infrastructure.migrations import PostgresReadinessProbe
from officeagent.infrastructure.postgres_corpus_store import PostgresCorpusStore
from tests.helpers import FakeLlmGateway, make_text_pdf

DATABASE_URL = os.environ.get("OFFICEAGENT_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the migrated Compose test database",
)

_VOCABULARY = (
    "교육",
    "복지",
    "배포",
    "승인",
    "휴가",
    "보안",
    "deployment",
    "reviewers",
)


@dataclass(frozen=True)
class KeywordEmbedding384Provider:
    """Deterministic 384-d model double; every infrastructure boundary stays real."""

    vocabulary: tuple[str, ...] = _VOCABULARY

    @property
    def dimension(self) -> int:
        return 384

    @property
    def embedding_version(self) -> str:
        return "keyword-384-v1:" + "|".join(self.vocabulary)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        folded = text.casefold()
        values = [0.0] * self.dimension
        for index, token in enumerate(self.vocabulary):
            values[index] = float(folded.count(token.casefold()))
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values


@pytest.fixture(autouse=True)
def clean_postgres_corpus() -> Iterator[None]:
    if DATABASE_URL is None:
        yield
        return
    _reset_database()
    try:
        yield
    finally:
        _reset_database()


def _reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            TRUNCATE cache_dependencies, answer_cache, document_chunks, documents
            CASCADE
            """
        )
        connection.execute(
            "UPDATE corpus_state SET revision = 0, updated_at = now() WHERE singleton"
        )


def _build_services() -> tuple[ServiceContainer, FakeLlmGateway, CacheVersions]:
    assert DATABASE_URL is not None
    embeddings = KeywordEmbedding384Provider()
    llm = FakeLlmGateway(deltas=("근거 기반 ", "테스트 답변"))
    cache_versions = CacheVersions(
        prompt_version=PROMPT_TEMPLATE_VERSION,
        embedding_version=embeddings.embedding_version,
        retrieval_version=build_retrieval_version(
            limit=3,
            minimum_score=0.30,
            cache_threshold=0.95,
        ),
    )
    corpus = PostgresCorpusStore(DATABASE_URL)
    services = ServiceContainer(
        ingestion=TransactionalIngestionService(
            corpus,
            embeddings,
            chunk_size=500,
            chunk_overlap=50,
            max_upload_bytes=1_000_000,
            cache_versions=cache_versions,
            retrieval_threshold=0.30,
        ),
        questions=QuestionAnsweringService(
            corpus,
            corpus,
            embeddings,
            llm,
            retrieval_limit=3,
            minimum_relevance_score=0.30,
            cache_similarity_threshold=0.95,
            cache_versions=cache_versions,
            corpus_store=corpus,
        ),
        readiness=PostgresReadinessProbe(DATABASE_URL),
    )
    return services, llm, cache_versions


async def _upload(
    client: AsyncClient,
    *,
    filename: str,
    content: bytes,
    content_type: str,
):
    return await client.post(
        "/v1/documents",
        files={"file": (filename, content, content_type)},
    )


async def test_http_ingestion_uses_real_txt_pdf_parser_migration_and_postgres() -> None:
    assert DATABASE_URL is not None
    services, llm, _ = _build_services()
    app = create_app(services)
    pdf = make_text_pdf("Deployment approval requires two reviewers.")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            text_response = await _upload(
                client,
                filename="교육-복지.txt",
                content="교육 복지 한도는 연 200만원입니다.".encode(),
                content_type="text/plain",
            )
            pdf_response = await _upload(
                client,
                filename="deployment.pdf",
                content=pdf,
                content_type="application/pdf",
            )
            unchanged_response = await _upload(
                client,
                filename="교육-복지.txt",
                content="교육 복지 한도는 연 200만원입니다.".encode(),
                content_type="text/plain",
            )
            ready_response = await client.get("/health/ready")

    assert text_response.status_code == 201
    assert pdf_response.status_code == 201
    assert unchanged_response.status_code == 201
    assert text_response.json()["change"] == "created"
    assert text_response.json()["chunk_count"] >= 1
    assert pdf_response.json()["change"] == "created"
    assert pdf_response.json()["chunk_count"] >= 1
    assert unchanged_response.json()["change"] == "unchanged"
    assert unchanged_response.json()["document_version"] == 1
    assert ready_response.status_code == 200
    assert ready_response.json() == {
        "status": "ready",
        "migration_head": "0001",
        "vector_version": "0.8.6",
    }
    assert llm.prompts == []

    with psycopg.connect(DATABASE_URL) as connection:
        documents = connection.execute(
            "SELECT filename, version, ready FROM documents ORDER BY filename"
        ).fetchall()
        pdf_page = connection.execute(
            """
            SELECT chunk.page
            FROM document_chunks AS chunk
            JOIN documents AS document
              ON document.document_id = chunk.document_id
             AND document.version = chunk.document_version
            WHERE document.filename = 'deployment.pdf'
            """
        ).fetchone()
        migration_count = connection.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()
        vector_version = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()

    assert documents == [
        ("deployment.pdf", 1, True),
        ("교육-복지.txt", 1, True),
    ]
    assert pdf_page == (1,)
    assert migration_count is not None and migration_count[0] >= 1
    assert vector_version is not None


async def test_http_rag_exact_similar_cache_and_document_invalidation() -> None:
    assert DATABASE_URL is not None
    services, llm, cache_versions = _build_services()
    app = create_app(services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for filename, text in (
                ("benefits.txt", "교육 복지 한도는 연 200만원입니다."),
                ("deployment.txt", "배포 승인은 두 명의 검토자가 필요합니다."),
                ("vacation.txt", "휴가 신청은 근무일 기준 사흘 전까지 제출합니다."),
            ):
                uploaded = await _upload(
                    client,
                    filename=filename,
                    content=text.encode(),
                    content_type="text/plain",
                )
                assert uploaded.status_code == 201

            first = await client.post(
                "/v1/questions",
                json={"question": "교육 복지 한도는 얼마인가요?"},
            )
            exact = await client.post(
                "/v1/questions",
                json={"question": "교육 복지 한도는 얼마인가요?"},
            )
            similar = await client.post(
                "/v1/questions",
                json={"question": "교육 복지 지원 금액을 알려줘"},
            )
            no_evidence = await client.post(
                "/v1/questions",
                json={"question": "구내식당 메뉴가 무엇인가요?"},
            )
            updated = await _upload(
                client,
                filename="benefits.txt",
                content="교육 복지 한도는 연 300만원입니다.".encode(),
                content_type="text/plain",
            )
            after_update = await client.post(
                "/v1/questions",
                json={"question": "교육 복지 한도는 얼마인가요?"},
            )

    first_body = first.json()
    exact_body = exact.json()
    similar_body = similar.json()
    no_evidence_body = no_evidence.json()
    after_update_body = after_update.json()

    assert first.status_code == 200
    assert first_body["cache_hit"] is False
    assert first_body["cache_written"] is True
    assert first_body["grounded"] is True
    assert first_body["citations"][0]["filename"] == "benefits.txt"
    assert first_body["citations"][0]["document_version"] == 1
    assert exact_body["cache_hit"] is True
    assert exact_body["cache_match"] == "exact"
    assert similar_body["cache_hit"] is True
    assert similar_body["cache_match"] == "similar"
    assert no_evidence_body["answer"] == NO_EVIDENCE_ANSWER
    assert no_evidence_body["grounded"] is False
    assert no_evidence_body["cache_skip_reason"] == "no_evidence"
    assert updated.status_code == 201
    assert updated.json()["change"] == "updated"
    assert updated.json()["invalidated_cache_entries"] == 1
    assert after_update_body["cache_hit"] is False
    assert after_update_body["cache_written"] is True
    assert after_update_body["citations"][0]["document_version"] == 2
    assert len(llm.prompts) == 2

    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            """
            SELECT active, prompt_version, embedding_version, retrieval_version
            FROM answer_cache
            ORDER BY created_at
            """
        ).fetchall()

    assert rows == [
        (
            False,
            cache_versions.prompt_version,
            cache_versions.embedding_version,
            cache_versions.retrieval_version,
        ),
        (
            True,
            cache_versions.prompt_version,
            cache_versions.embedding_version,
            cache_versions.retrieval_version,
        ),
    ]


async def test_sse_uses_the_same_postgres_cache_pipeline() -> None:
    services, llm, _ = _build_services()
    app = create_app(services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            uploaded = await _upload(
                client,
                filename="security.txt",
                content="보안 승인은 담당자 두 명이 검토합니다.".encode(),
                content_type="text/plain",
            )
            first = await client.post(
                "/v1/questions/stream",
                json={"question": "보안 승인 절차는?"},
            )
            cached = await client.post(
                "/v1/questions/stream",
                json={"question": "보안 승인 절차는?"},
            )

    assert uploaded.status_code == 201
    assert first.status_code == 200
    assert "event: answer.started" in first.text
    assert '"cache_hit":false' in first.text
    assert "event: answer.delta" in first.text
    assert "event: answer.citation" in first.text
    assert "event: answer.completed" in first.text
    assert cached.status_code == 200
    assert '"cache_hit":true' in cached.text
    assert '"cache_match":"exact"' in cached.text
    assert len(llm.prompts) == 1
