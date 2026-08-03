"""Tokenizer-proxy prompt packing and cache-identity boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from officeagent.api.app import ServiceContainer, create_app
from officeagent.application.ingestion import DocumentIngestion
from officeagent.application.observability import PipelineTimingReceipt
from officeagent.application.prompts import (
    PromptTokenCountError,
    build_grounded_prompt,
    build_prompt_cache_version,
    pack_grounded_prompt,
)
from officeagent.application.question_answering import (
    PROMPT_BUDGET_ANSWER,
    QuestionAnsweringService,
    build_retrieval_version,
)
from officeagent.domain.models import CacheVersions, Chunk, SearchHit
from officeagent.infrastructure import tokenization
from officeagent.infrastructure.settings import Settings
from officeagent.infrastructure.tokenization import (
    PROMPT_TOKENIZER_ENCODING,
    PROMPT_TOKENIZER_POLICY_VERSION,
    TOKENIZER_ENCODING,
    TOKENIZER_POLICY_VERSION,
    TiktokenPromptTokenCounter,
    TiktokenTokenCounter,
)
from tests.helpers import FakeLlmGateway, StaticReadinessProbe


@dataclass(frozen=True)
class _WhitespaceTokenCounter:
    token_counter_version: str = "whitespace-test-v1"

    async def count_tokens(self, text: str) -> int:
        return len(text.split())


@dataclass(frozen=True)
class _FailingTokenCounter:
    token_counter_version: str = "failing-test-v1"

    async def count_tokens(self, text: str) -> int:
        del text
        raise ValueError("sensitive tokenizer detail")


@dataclass(frozen=True)
class _InvalidTokenCounter:
    token_counter_version: str = "invalid-test-v1"

    async def count_tokens(self, text: str) -> int:
        del text
        return -1


@dataclass
class _TiktokenEncodingStub:
    seen: list[str]

    def encode_ordinary(self, text: str) -> list[int]:
        self.seen.append(text)
        return list(range(len(text.split())))


def _hit(index: int, text: str, *, score: float = 0.9) -> SearchHit:
    return SearchHit(
        chunk=Chunk(
            id=f"doc:{index}:0",
            document_id=f"doc:{index}",
            document_version=1,
            filename=f"document-{index}.txt",
            text=text,
            chunk_index=0,
            page_number=None,
            start_offset=0,
            end_offset=len(text),
        ),
        score=score,
    )


async def test_packer_preserves_rank_while_skipping_an_oversized_hit() -> None:
    counter = _WhitespaceTokenCounter()
    version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=2_000,
    )
    first = _hit(1, "first compact evidence")
    oversized = _hit(2, "oversized " * 2_000, score=0.8)
    third = _hit(3, "third compact evidence", score=0.7)
    expected_prompt = build_grounded_prompt(
        "question",
        [first, third],
        prompt_version=version,
    )
    budget = await counter.count_tokens(expected_prompt)

    package = await pack_grounded_prompt(
        "question",
        [first, oversized, third],
        token_counter=counter,
        input_token_budget=budget,
        prompt_version=version,
    )

    assert package.hits == (first, third)
    assert package.prompt == expected_prompt
    assert package.input_tokens <= budget
    assert "oversized" not in package.prompt


async def test_packer_returns_no_prompt_when_no_evidence_record_fits() -> None:
    counter = _WhitespaceTokenCounter()
    version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=512,
    )
    empty_prompt = build_grounded_prompt("question", [], prompt_version=version)
    budget = await counter.count_tokens(empty_prompt)

    package = await pack_grounded_prompt(
        "question",
        [_hit(1, "one evidence token")],
        token_counter=counter,
        input_token_budget=budget,
        prompt_version=version,
    )

    assert package.prompt is None
    assert package.hits == ()
    assert package.input_tokens == budget


def test_prompt_cache_version_changes_with_budget_and_counter_without_raw_identity() -> None:
    first = build_prompt_cache_version(
        token_counter_version="model/name\noperator-controlled",
        input_token_budget=4_096,
    )
    different_budget = build_prompt_cache_version(
        token_counter_version="model/name\noperator-controlled",
        input_token_budget=8_192,
    )
    different_counter = build_prompt_cache_version(
        token_counter_version="other-model",
        input_token_budget=4_096,
    )

    assert first != different_budget
    assert first != different_counter
    assert "model/name" not in first
    assert "\n" not in first


async def test_tiktoken_adapter_counts_the_complete_prompt_without_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoding = _TiktokenEncodingStub([])
    monkeypatch.setattr(tokenization, "get_encoding", lambda name: encoding)
    counter = TiktokenTokenCounter()
    prompt = "token " * 10_000

    assert await counter.count_tokens(prompt) == 10_000
    assert encoding.seen == [prompt]
    assert counter.token_counter_version.startswith("tiktoken:")


def test_prompt_tokenizer_names_remain_compatible() -> None:
    assert TiktokenPromptTokenCounter is TiktokenTokenCounter
    assert PROMPT_TOKENIZER_ENCODING == TOKENIZER_ENCODING
    assert PROMPT_TOKENIZER_POLICY_VERSION == TOKENIZER_POLICY_VERSION


def test_prompt_budget_setting_has_bounded_default() -> None:
    assert Settings(_env_file=None).prompt_input_token_budget == 4_096
    with pytest.raises(ValidationError):
        Settings(prompt_input_token_budget=511, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(prompt_input_token_budget=65_537, _env_file=None)


@dataclass
class _MetadataStore:
    saved_citation_filenames: tuple[str, ...] = ()

    async def find_cached_answer(self, **kwargs):
        del kwargs
        return None

    async def save_cached_answer(self, **kwargs) -> str:
        self.saved_citation_filenames = tuple(
            citation.filename for citation in kwargs["citations"]
        )
        return "cache-1"


@dataclass
class _TimingCollector:
    receipts: list[PipelineTimingReceipt]

    def record(self, receipt: PipelineTimingReceipt) -> None:
        self.receipts.append(receipt)


@dataclass(frozen=True)
class _VectorStore:
    hits: tuple[SearchHit, ...]

    async def search(self, query_embedding, *, limit: int) -> list[SearchHit]:
        del query_embedding
        return list(self.hits[:limit])


@dataclass(frozen=True)
class _EmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 1

    @property
    def embedding_version(self) -> str:
        return "embedding-test-v1"

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0]

    async def embed_documents(self, texts) -> list[list[float]]:
        return [[1.0] for _ in texts]


async def test_question_path_abstains_without_llm_or_cache_when_prompt_budget_exhausts() -> None:
    counter = _WhitespaceTokenCounter()
    preliminary_version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=512,
    )
    empty_prompt = build_grounded_prompt(
        "question",
        [],
        prompt_version=preliminary_version,
    )
    budget = await counter.count_tokens(empty_prompt)
    prompt_version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=budget,
    )
    empty_prompt = build_grounded_prompt("question", [], prompt_version=prompt_version)
    assert await counter.count_tokens(empty_prompt) == budget
    metadata = _MetadataStore()
    llm = FakeLlmGateway()
    timing = _TimingCollector([])
    retrieval_version = build_retrieval_version(
        limit=5,
        minimum_score=0.3,
        cache_threshold=0.95,
        chunking_version="test-chunking-v1",
    )
    service = QuestionAnsweringService(
        metadata,
        _VectorStore((_hit(1, "evidence that cannot fit"),)),
        _EmbeddingProvider(),
        llm,
        retrieval_limit=5,
        minimum_relevance_score=0.3,
        cache_similarity_threshold=0.95,
        cache_versions=CacheVersions(
            prompt_version=prompt_version,
            embedding_version="embedding-test-v1",
            retrieval_version=retrieval_version,
        ),
        token_counter=counter,
        prompt_input_token_budget=budget,
        timing_recorder=timing,
    )

    answer = await service.answer("question")

    assert answer.answer == PROMPT_BUDGET_ANSWER
    assert answer.grounded is False
    assert answer.citations == ()
    assert answer.cache_written is False
    assert answer.cache_skip_reason == "prompt_budget_exceeded"
    assert llm.prompts == []
    assert metadata.saved_citation_filenames == ()
    assert timing.receipts[-1].answer_path == "prompt_budget"


async def test_question_path_cites_only_fitting_evidence_and_skips_cache() -> None:
    counter = _WhitespaceTokenCounter()
    first = _hit(1, "first compact evidence")
    oversized = _hit(2, "oversized " * 2_000, score=0.8)
    third = _hit(3, "third compact evidence", score=0.7)
    preliminary_version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=2_000,
    )
    expected_prompt = build_grounded_prompt(
        "question",
        [first, third],
        prompt_version=preliminary_version,
    )
    budget = await counter.count_tokens(expected_prompt)
    prompt_version = build_prompt_cache_version(
        token_counter_version=counter.token_counter_version,
        input_token_budget=budget,
    )
    expected_prompt = build_grounded_prompt(
        "question",
        [first, third],
        prompt_version=prompt_version,
    )
    assert await counter.count_tokens(expected_prompt) == budget
    metadata = _MetadataStore()
    llm = FakeLlmGateway()
    service = QuestionAnsweringService(
        metadata,
        _VectorStore((first, oversized, third)),
        _EmbeddingProvider(),
        llm,
        retrieval_limit=5,
        minimum_relevance_score=0.3,
        cache_similarity_threshold=0.95,
        cache_versions=CacheVersions(
            prompt_version=prompt_version,
            embedding_version="embedding-test-v1",
            retrieval_version=build_retrieval_version(
                limit=5,
                minimum_score=0.3,
                cache_threshold=0.95,
                chunking_version="test-chunking-v1",
            ),
        ),
        token_counter=counter,
        prompt_input_token_budget=budget,
    )

    answer = await service.answer("question")

    assert tuple(citation.filename for citation in answer.citations) == (
        "document-1.txt",
        "document-3.txt",
    )
    assert answer.cache_written is False
    assert answer.cache_skip_reason == "prompt_evidence_reduced"
    assert metadata.saved_citation_filenames == ()
    assert llm.prompts == [expected_prompt]
    assert "oversized" not in expected_prompt


def test_question_service_requires_complete_prompt_packing_configuration() -> None:
    common = {
        "retrieval_limit": 5,
        "minimum_relevance_score": 0.3,
        "cache_similarity_threshold": 0.95,
    }

    with pytest.raises(ValueError, match="must be configured together"):
        QuestionAnsweringService(
            _MetadataStore(),
            _VectorStore(()),
            _EmbeddingProvider(),
            FakeLlmGateway(),
            token_counter=_WhitespaceTokenCounter(),
            **common,
        )
    with pytest.raises(ValueError, match="must be configured together"):
        QuestionAnsweringService(
            _MetadataStore(),
            _VectorStore(()),
            _EmbeddingProvider(),
            FakeLlmGateway(),
            prompt_input_token_budget=512,
            **common,
        )


async def test_token_counter_failure_does_not_call_llm_or_write_cache() -> None:
    counter = _FailingTokenCounter()
    metadata = _MetadataStore()
    llm = FakeLlmGateway()
    timing = _TimingCollector([])
    service = QuestionAnsweringService(
        metadata,
        _VectorStore((_hit(1, "relevant evidence"),)),
        _EmbeddingProvider(),
        llm,
        retrieval_limit=5,
        minimum_relevance_score=0.3,
        cache_similarity_threshold=0.95,
        token_counter=counter,
        prompt_input_token_budget=512,
        timing_recorder=timing,
    )

    with pytest.raises(PromptTokenCountError, match="prompt token counting failed"):
        await service.answer("question")

    assert llm.prompts == []
    assert metadata.saved_citation_filenames == ()
    assert timing.receipts[-1].outcome == "error"
    assert timing.receipts[-1].error_type == "PromptTokenCountError"


async def test_invalid_token_count_is_hidden_as_internal_json_and_sse_failure() -> None:
    counter = _InvalidTokenCounter()
    metadata = _MetadataStore()
    llm = FakeLlmGateway()
    questions = QuestionAnsweringService(
        metadata,
        _VectorStore((_hit(1, "relevant evidence"),)),
        _EmbeddingProvider(),
        llm,
        retrieval_limit=5,
        minimum_relevance_score=0.3,
        cache_similarity_threshold=0.95,
        token_counter=counter,
        prompt_input_token_budget=512,
    )
    app = create_app(
        ServiceContainer(
            ingestion=cast(DocumentIngestion, object()),
            questions=questions,
            readiness=StaticReadinessProbe(),
        )
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            collected = await client.post(
                "/v1/questions",
                json={"question": "question"},
            )
            streamed = await client.post(
                "/v1/questions/stream",
                json={"question": "question"},
            )

    assert collected.status_code == 502
    assert collected.json() == {"detail": "Answer generation failed"}
    assert streamed.status_code == 200
    assert "event: answer.started" in streamed.text
    assert "event: answer.error" in streamed.text
    assert "answer_generation_failed" in streamed.text
    assert "invalid count" not in streamed.text
    assert llm.prompts == []
    assert metadata.saved_citation_filenames == ()
