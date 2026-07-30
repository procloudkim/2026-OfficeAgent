"""Deterministic test doubles and fixture builders."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from officeagent.api.app import ServiceContainer
from officeagent.application.ingestion import IngestionService
from officeagent.application.prompts import PROMPT_TEMPLATE_VERSION
from officeagent.application.question_answering import (
    QuestionAnsweringService,
    build_retrieval_version,
)
from officeagent.domain.models import CacheVersions
from officeagent.infrastructure.chroma_store import ChromaVectorStore
from officeagent.infrastructure.sqlite_store import SqliteMetadataStore


@dataclass
class KeywordEmbeddingProvider:
    """Small deterministic embedding with no model download."""

    vocabulary: tuple[str, ...]

    @property
    def dimension(self) -> int:
        return len(self.vocabulary)

    @property
    def embedding_version(self) -> str:
        return "keyword-v1:" + "|".join(self.vocabulary)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        folded = text.casefold()
        values = [float(folded.count(token.casefold())) for token in self.vocabulary]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values


@dataclass
class FakeLlmGateway:
    """Record prompts and yield preselected genuine async chunks."""

    deltas: tuple[str, ...] = ("근거에 따른 ", "답변입니다.")
    prompts: list[str] = field(default_factory=list)

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        for delta in self.deltas:
            yield delta


@dataclass
class ForbiddenLlmGateway:
    """Fail loudly when an abstention or cache path invokes the LLM."""

    calls: int = 0

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        del prompt
        self.calls += 1
        raise AssertionError("LLM must not be called on this path")
        yield  # pragma: no cover


@dataclass(frozen=True)
class TestRuntime:
    """Concrete local stores plus the application service container."""

    services: ServiceContainer
    metadata: SqliteMetadataStore
    vectors: ChromaVectorStore
    embeddings: KeywordEmbeddingProvider
    cache_versions: CacheVersions


async def build_test_runtime(
    temporary_directory: Path,
    *,
    llm: FakeLlmGateway | ForbiddenLlmGateway | None = None,
    vocabulary: tuple[str, ...] = ("education", "deployment", "vacation", "security"),
    retrieval_limit: int = 5,
    minimum_relevance_score: float = 0.5,
    cache_similarity_threshold: float = 0.95,
) -> TestRuntime:
    """Wire actual SQLite and Chroma with deterministic fake model boundaries."""

    metadata = SqliteMetadataStore(temporary_directory / "metadata.sqlite3")
    await metadata.initialize()
    vectors = ChromaVectorStore(temporary_directory / "chroma", "test_document_chunks")
    embeddings = KeywordEmbeddingProvider(vocabulary)
    llm_gateway = llm or FakeLlmGateway()
    cache_versions = CacheVersions(
        prompt_version=PROMPT_TEMPLATE_VERSION,
        embedding_version=embeddings.embedding_version,
        retrieval_version=build_retrieval_version(
            limit=retrieval_limit,
            minimum_score=minimum_relevance_score,
            cache_threshold=cache_similarity_threshold,
        ),
    )
    services = ServiceContainer(
        ingestion=IngestionService(
            metadata,
            vectors,
            embeddings,
            chunk_size=300,
            chunk_overlap=50,
            max_upload_bytes=1_000_000,
        ),
        questions=QuestionAnsweringService(
            metadata,
            vectors,
            embeddings,
            llm_gateway,
            retrieval_limit=retrieval_limit,
            minimum_relevance_score=minimum_relevance_score,
            cache_similarity_threshold=cache_similarity_threshold,
            cache_versions=cache_versions,
        ),
    )
    return TestRuntime(
        services=services,
        metadata=metadata,
        vectors=vectors,
        embeddings=embeddings,
        cache_versions=cache_versions,
    )


def make_text_pdf(text: str) -> bytes:
    """Create a tiny, extractable single-page PDF without an extra dependency."""

    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
