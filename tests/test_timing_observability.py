"""Deterministic, content-free timing receipts for the application pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from officeagent.api.app import ServiceContainer, create_app
from officeagent.application.ingestion import IngestionService
from officeagent.application.observability import (
    JsonTimingRecorder,
    PipelineOperation,
    PipelineOutcome,
    PipelineTimingReceipt,
)
from officeagent.application.question_answering import QuestionAnsweringService
from officeagent.application.transactional_ingestion import TransactionalIngestionService
from officeagent.domain.models import (
    CacheVersions,
    DocumentChange,
    DocumentRecord,
    IngestionResult,
    PreparedChunk,
)
from tests.helpers import FakeLlmGateway, build_test_runtime


@dataclass
class _StepClock:
    """Advance one millisecond per read so timing assertions are wall-clock free."""

    current_ns: int = -1_000_000

    def __call__(self) -> int:
        self.current_ns += 1_000_000
        return self.current_ns


@dataclass
class _ManualClock:
    current_ns: int = 0

    def __call__(self) -> int:
        return self.current_ns

    def advance_ms(self, milliseconds: int) -> None:
        self.current_ns += milliseconds * 1_000_000


@dataclass
class _ReceiptCollector:
    receipts: list[PipelineTimingReceipt] = field(default_factory=list)

    def record(self, receipt: PipelineTimingReceipt) -> None:
        self.receipts.append(receipt)


class _FailingRecorder:
    def record(self, receipt: PipelineTimingReceipt) -> None:
        del receipt
        raise RuntimeError("observer unavailable")


@dataclass
class _CharacterTokenLimiter:
    thread_ids: list[int] = field(default_factory=list)

    @property
    def token_counter_version(self) -> str:
        return "character-test-v1"

    def bounded_prefix(self, text: str, *, max_tokens: int) -> str:
        self.thread_ids.append(threading.get_ident())
        return text[:max_tokens]


@dataclass
class _CorpusWriter:
    calls: int = 0

    async def commit_document(
        self,
        *,
        filename: str,
        content_type: str,
        content_hash: str,
        chunking_version: str,
        chunks: Sequence[PreparedChunk],
        embeddings: Sequence[Sequence[float]],
        cache_versions: CacheVersions,
        retrieval_threshold: float,
    ) -> IngestionResult:
        del (
            chunking_version,
            embeddings,
            cache_versions,
            retrieval_threshold,
        )
        self.calls += 1
        now = datetime(2026, 8, 3, tzinfo=UTC)
        return IngestionResult(
            document=DocumentRecord(
                id="document-1",
                filename=filename,
                content_type=content_type,
                content_hash=content_hash,
                version=1,
                created_at=now,
                updated_at=now,
            ),
            change=DocumentChange.CREATED,
            chunk_count=len(chunks),
            invalidated_cache_entries=0,
        )


def _request_id(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


async def test_question_timing_distinguishes_live_generation_from_cache_replay(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    llm = FakeLlmGateway(("grounded ", "answer"))
    collector = _ReceiptCollector()
    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        llm,
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=_StepClock(),
    )

    first = await service.answer(
        "Explain the education policy",
        request_id=_request_id(1),
    )
    cached = await service.answer(
        "Explain the education policy",
        request_id=_request_id(2),
    )

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert len(llm.prompts) == 1
    live_receipt, cache_receipt = collector.receipts
    assert live_receipt.to_dict() == {
        "answer_path": "grounded",
        "cache_hit": False,
        "cache_match": None,
        "event": "officeagent.pipeline_timing",
        "operation": "question_answering",
        "outcome": "completed",
        "request_id": _request_id(1),
        "schema_version": 1,
        "stages_ms": {
            "cache_lookup": 1.0,
            "cache_write": 1.0,
            "embed": 1.0,
            "llm": 3.0,
            "retrieve": 1.0,
        },
        "total_ms": 15.0,
    }
    assert cache_receipt.to_dict() == {
        "answer_path": "cache_hit",
        "cache_hit": True,
        "cache_match": "exact",
        "event": "officeagent.pipeline_timing",
        "operation": "question_answering",
        "outcome": "completed",
        "request_id": _request_id(2),
        "schema_version": 1,
        "stages_ms": {"cache_lookup": 1.0, "embed": 1.0},
        "total_ms": 5.0,
    }
    assert "retrieve" not in cache_receipt.stages_ms
    assert "llm" not in cache_receipt.stages_ms


async def test_ingestion_timing_records_parse_embed_and_persist_without_content(
    test_data_dir,
    caplog,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    recorder = JsonTimingRecorder(logging.getLogger("test.pipeline-timing"))
    ingestion = IngestionService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        chunk_size=300,
        chunk_overlap=50,
        max_upload_bytes=1_000_000,
        timing_recorder=recorder,
        clock=_StepClock(),
    )
    secret_filename = "private-payroll.txt"
    secret_content = b"Education allowance for employee 7419 is secret."

    with caplog.at_level(logging.INFO, logger="test.pipeline-timing"):
        await ingestion.ingest(
            filename=secret_filename,
            content_type="text/plain",
            content=secret_content,
            request_id=_request_id(3),
        )

    payload = json.loads(caplog.messages[-1])
    assert payload == {
        "answer_path": None,
        "cache_hit": None,
        "cache_match": None,
        "event": "officeagent.pipeline_timing",
        "operation": "document_ingestion",
        "outcome": "completed",
        "request_id": _request_id(3),
        "schema_version": 1,
        "stages_ms": {"embed": 1.0, "parse": 1.0, "persist": 2.0},
        "total_ms": 9.0,
    }
    serialized = caplog.messages[-1]
    assert secret_filename not in serialized
    assert secret_content.decode() not in serialized
    assert "education" not in serialized.casefold()


async def test_transactional_ingestion_uses_the_same_stage_contract(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    writer = _CorpusWriter()
    collector = _ReceiptCollector()
    token_limiter = _CharacterTokenLimiter()
    event_loop_thread = threading.get_ident()
    ingestion = TransactionalIngestionService(
        writer,
        runtime.embeddings,
        chunk_size=300,
        chunk_overlap=50,
        token_limiter=token_limiter,
        max_upload_bytes=1_000_000,
        cache_versions=runtime.cache_versions,
        retrieval_threshold=0.5,
        timing_recorder=collector,
        clock=_StepClock(),
    )

    result = await ingestion.ingest(
        filename="private-policy.txt",
        content_type="text/plain",
        content=b"Education allowance for employee 7419 is secret.",
        request_id=_request_id(11),
    )

    assert result.chunk_count == 1
    assert writer.calls == 1
    assert token_limiter.thread_ids
    assert all(thread_id != event_loop_thread for thread_id in token_limiter.thread_ids)
    assert collector.receipts[0].to_dict() == {
        "answer_path": None,
        "cache_hit": None,
        "cache_match": None,
        "event": "officeagent.pipeline_timing",
        "operation": "document_ingestion",
        "outcome": "completed",
        "request_id": _request_id(11),
        "schema_version": 1,
        "stages_ms": {"embed": 1.0, "parse": 2.0, "persist": 1.0},
        "total_ms": 9.0,
    }


async def test_transactional_ingestion_rejects_embedding_version_drift(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    mismatched_versions = CacheVersions(
        prompt_version=runtime.cache_versions.prompt_version,
        embedding_version="different-embedding-version",
        retrieval_version=runtime.cache_versions.retrieval_version,
    )

    with pytest.raises(ValueError, match="embedding versions must match"):
        TransactionalIngestionService(
            _CorpusWriter(),
            runtime.embeddings,
            chunk_size=300,
            chunk_overlap=50,
            token_limiter=_CharacterTokenLimiter(),
            max_upload_bytes=1_000_000,
            cache_versions=mismatched_versions,
            retrieval_threshold=0.5,
        )


async def test_error_receipt_sanitizes_request_id_and_exception_message(
    test_data_dir,
    caplog,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    recorder = JsonTimingRecorder(logging.getLogger("test.pipeline-error"))
    ingestion = IngestionService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        chunk_size=300,
        chunk_overlap=50,
        max_upload_bytes=1_000_000,
        timing_recorder=recorder,
        clock=_StepClock(),
    )
    unsafe_request_id = "question=employee 7419 salary"

    with caplog.at_level(logging.INFO, logger="test.pipeline-error"):
        try:
            await ingestion.ingest(
                filename="unsupported.secret",
                content_type="application/octet-stream",
                content=b"employee 7419 salary",
                request_id=unsafe_request_id,
            )
        except ValueError:
            pass

    payload = json.loads(caplog.messages[-1])
    assert payload["outcome"] == "error"
    assert payload["error_type"] == "UnsupportedDocumentError"
    assert payload["request_id"] == "invalid"
    assert payload["stages_ms"] == {"parse": 1.0}
    serialized = caplog.messages[-1]
    assert "7419" not in serialized
    assert "salary" not in serialized


async def test_no_evidence_and_consumer_cancellation_have_explicit_paths(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    collector = _ReceiptCollector()
    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        FakeLlmGateway(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=_StepClock(),
    )

    no_evidence = await service.answer(
        "What is served in the cafeteria?",
        request_id=_request_id(4),
    )
    no_evidence_receipt = collector.receipts[-1]
    assert no_evidence.grounded is False
    assert no_evidence_receipt.answer_path == "no_evidence"
    assert no_evidence_receipt.cache_hit is False
    assert set(no_evidence_receipt.stages_ms) == {
        "embed",
        "cache_lookup",
        "retrieve",
    }

    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    stream = service.stream(
        "Explain the education policy",
        request_id=_request_id(5),
    )
    started = await anext(stream)
    assert started.payload["cache_hit"] is False
    await stream.aclose()

    cancelled_receipt = collector.receipts[-1]
    assert cancelled_receipt.outcome == "cancelled"
    assert cancelled_receipt.error_type == "GeneratorExit"
    assert "llm" not in cancelled_receipt.stages_ms


async def test_recorder_failure_does_not_change_answer_semantics(test_data_dir) -> None:
    runtime = await build_test_runtime(test_data_dir)
    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        FakeLlmGateway(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=_FailingRecorder(),
        clock=_StepClock(),
    )

    result = await service.answer("No matching corpus", request_id=_request_id(6))

    assert result.grounded is False


async def test_cancelled_error_is_not_reclassified_as_application_failure(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    collector = _ReceiptCollector()

    class _CancelledLlm:
        async def stream_answer(self, prompt: str):
            del prompt
            raise asyncio.CancelledError
            yield  # pragma: no cover

    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        _CancelledLlm(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=_StepClock(),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.answer("Explain education", request_id=_request_id(7))

    receipt = collector.receipts[-1]
    assert receipt.outcome == "cancelled"
    assert receipt.error_type == "CancelledError"
    assert receipt.cache_hit is False
    assert receipt.answer_path == "grounded"
    assert "llm" in receipt.stages_ms


async def test_terminal_event_is_published_only_after_receipt_is_recorded(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    collector = _ReceiptCollector()
    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        FakeLlmGateway(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=_StepClock(),
    )
    stream = service.stream("Explain education", request_id=_request_id(8))

    while True:
        event = await anext(stream)
        if event.type == "answer.completed":
            break

    assert len(collector.receipts) == 1
    await stream.aclose()
    assert len(collector.receipts) == 1
    assert collector.receipts[0].outcome == "completed"


async def test_llm_stage_excludes_downstream_consumer_suspension(test_data_dir) -> None:
    runtime = await build_test_runtime(test_data_dir)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    clock = _ManualClock()
    collector = _ReceiptCollector()

    class _ClockedLlm:
        async def stream_answer(self, prompt: str):
            del prompt
            clock.advance_ms(2)
            yield "first"
            clock.advance_ms(3)
            yield "second"

    service = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        _ClockedLlm(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=clock,
    )

    async for event in service.stream("Explain education", request_id=_request_id(12)):
        if event.type == "answer.delta":
            clock.advance_ms(100)

    receipt = collector.receipts[0]
    assert receipt.stages_ms["llm"] == 5.0
    assert receipt.total_ms == 205.0


async def test_http_response_and_timing_receipt_share_one_request_id(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    collector = _ReceiptCollector()
    questions = QuestionAnsweringService(
        runtime.metadata,
        runtime.vectors,
        runtime.embeddings,
        FakeLlmGateway(),
        retrieval_limit=5,
        minimum_relevance_score=0.5,
        cache_similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
        timing_recorder=collector,
        clock=_StepClock(),
    )
    services = ServiceContainer(
        ingestion=runtime.services.ingestion,
        questions=questions,
        readiness=runtime.services.readiness,
    )
    app = create_app(services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/questions",
                json={"question": "No matching corpus"},
            )

    assert response.status_code == 200
    response_request_id = response.json()["request_id"]
    assert UUID(response_request_id).version == 4
    assert collector.receipts[0].request_id == response_request_id


@pytest.mark.parametrize("invalid_duration", [float("nan"), float("inf"), -0.001])
def test_receipt_rejects_non_finite_or_negative_durations(invalid_duration) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        PipelineTimingReceipt(
            operation=PipelineOperation.QUESTION_ANSWERING,
            request_id=_request_id(9),
            outcome=PipelineOutcome.COMPLETED,
            stages_ms={"embed": invalid_duration},
            total_ms=1.0,
        )


def test_receipt_total_must_cover_each_measured_stage() -> None:
    with pytest.raises(ValueError, match="total timing"):
        PipelineTimingReceipt(
            operation=PipelineOperation.QUESTION_ANSWERING,
            request_id=_request_id(10),
            outcome=PipelineOutcome.COMPLETED,
            stages_ms={"embed": 2.0},
            total_ms=1.0,
        )
