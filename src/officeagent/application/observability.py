"""Privacy-safe, transport-neutral timing receipts for application pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID, uuid4

_SAFE_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")
_LOGGER = logging.getLogger(__name__)

Clock = Callable[[], int]


class PipelineOperation(StrEnum):
    """Bounded operations emitted by the application layer."""

    DOCUMENT_INGESTION = "document_ingestion"
    QUESTION_ANSWERING = "question_answering"


class TimingStage(StrEnum):
    """Stable stage names; values never contain request or document data."""

    PARSE = "parse"
    EMBED = "embed"
    PERSIST = "persist"
    CACHE_LOOKUP = "cache_lookup"
    RETRIEVE = "retrieve"
    LLM = "llm"
    CACHE_WRITE = "cache_write"


class PipelineOutcome(StrEnum):
    """Terminal state for one timing receipt."""

    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class AnswerPath(StrEnum):
    """Controlled answer paths used to interpret absent stage timings."""

    CACHE_HIT = "cache_hit"
    GROUNDED = "grounded"
    NO_EVIDENCE = "no_evidence"
    PROMPT_BUDGET = "prompt_budget"


@dataclass(frozen=True, slots=True)
class PipelineTimingReceipt:
    """One aggregate receipt containing only bounded metadata and durations."""

    operation: PipelineOperation
    request_id: str
    outcome: PipelineOutcome
    stages_ms: Mapping[str, float]
    total_ms: float
    cache_hit: bool | None = None
    cache_match: str | None = None
    answer_path: AnswerPath | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        operation = PipelineOperation(self.operation)
        outcome = PipelineOutcome(self.outcome)
        answer_path = AnswerPath(self.answer_path) if self.answer_path is not None else None
        safe_stages = {
            TimingStage(stage).value: _validate_duration(duration)
            for stage, duration in self.stages_ms.items()
        }
        total_ms = _validate_duration(self.total_ms)
        if safe_stages and total_ms < max(safe_stages.values()):
            raise ValueError("total timing must cover every measured stage")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "request_id", _sanitize_request_id(self.request_id))
        object.__setattr__(
            self,
            "stages_ms",
            MappingProxyType(dict(sorted(safe_stages.items()))),
        )
        object.__setattr__(self, "total_ms", total_ms)
        object.__setattr__(self, "answer_path", answer_path)
        object.__setattr__(
            self,
            "cache_match",
            _sanitize_cache_match(self.cache_hit, self.cache_match),
        )
        object.__setattr__(self, "error_type", _sanitize_type_name(self.error_type))

    def to_dict(self) -> dict[str, object]:
        """Return the versioned JSON shape used by logs and test receipts."""

        payload: dict[str, object] = {
            "answer_path": self.answer_path.value if self.answer_path else None,
            "cache_hit": self.cache_hit,
            "cache_match": self.cache_match,
            "event": "officeagent.pipeline_timing",
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "request_id": self.request_id,
            "schema_version": 1,
            "stages_ms": dict(self.stages_ms),
            "total_ms": self.total_ms,
        }
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        return payload


class TimingRecorder(Protocol):
    """Sink boundary so tests and production choose their own receipt storage."""

    def record(self, receipt: PipelineTimingReceipt) -> None:
        """Record one immutable, already-sanitized receipt."""


class JsonTimingRecorder:
    """Emit one compact JSON object per completed or failed pipeline."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("officeagent.pipeline_timing")

    def record(self, receipt: PipelineTimingReceipt) -> None:
        self._logger.info(
            json.dumps(
                receipt.to_dict(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )


class PipelineTimer:
    """Measure named stages with an injectable monotonic nanosecond clock."""

    def __init__(
        self,
        operation: PipelineOperation,
        *,
        request_id: str | None,
        recorder: TimingRecorder | None,
        clock: Clock = time.perf_counter_ns,
    ) -> None:
        self._operation = operation
        self._request_id = _sanitize_request_id(request_id)
        self._recorder = recorder
        self._clock = clock
        self._started_ns = clock()
        self._durations_ns: dict[TimingStage, int] = {}
        self._finished = False

    @property
    def finished(self) -> bool:
        """Return whether the single terminal receipt has already been emitted."""

        return self._finished

    @contextmanager
    def measure(self, stage: TimingStage) -> Iterator[None]:
        """Accumulate repeated spans under one bounded stage name."""

        started_ns = self._clock()
        try:
            yield
        finally:
            duration_ns = max(0, self._clock() - started_ns)
            self._durations_ns[stage] = self._durations_ns.get(stage, 0) + duration_ns

    def finish(
        self,
        outcome: PipelineOutcome,
        *,
        cache_hit: bool | None = None,
        cache_match: str | None = None,
        answer_path: AnswerPath | None = None,
        error_type: str | None = None,
    ) -> PipelineTimingReceipt:
        """Build and best-effort publish exactly one terminal receipt."""

        if self._finished:
            raise RuntimeError("pipeline timing receipt is already finished")
        self._finished = True
        receipt = PipelineTimingReceipt(
            operation=self._operation,
            request_id=self._request_id,
            outcome=outcome,
            stages_ms={
                stage.value: _milliseconds(duration_ns)
                for stage, duration_ns in self._durations_ns.items()
            },
            total_ms=_milliseconds(max(0, self._clock() - self._started_ns)),
            cache_hit=cache_hit,
            cache_match=_sanitize_cache_match(cache_hit, cache_match),
            answer_path=answer_path,
            error_type=_sanitize_type_name(error_type),
        )
        if self._recorder is not None:
            try:
                self._recorder.record(receipt)
            except Exception as exc:  # Observability must not fail the product path.
                _LOGGER.warning(
                    "Pipeline timing recorder failed",
                    extra={"error_type": _sanitize_type_name(type(exc).__name__)},
                )
        return receipt


def outcome_for_exception(exc: BaseException) -> PipelineOutcome:
    """Classify cancellation without serializing exception messages."""

    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return PipelineOutcome.CANCELLED
    return PipelineOutcome.ERROR


def _sanitize_request_id(request_id: str | None) -> str:
    if request_id is None:
        return str(uuid4())
    try:
        parsed = UUID(request_id)
    except (AttributeError, TypeError, ValueError):
        return "invalid"
    canonical = str(parsed)
    return canonical if parsed.version == 4 and canonical == request_id.casefold() else "invalid"


def _sanitize_cache_match(cache_hit: bool | None, cache_match: str | None) -> str | None:
    if cache_hit is True and cache_match in {"exact", "similar"}:
        return cache_match
    return None


def _sanitize_type_name(error_type: str | None) -> str | None:
    if error_type is None:
        return None
    sanitized = _SAFE_TYPE_PATTERN.sub("_", error_type)[:80]
    return sanitized or "Error"


def _milliseconds(duration_ns: int) -> float:
    return round(duration_ns / 1_000_000, 3)


def _validate_duration(value: float) -> float:
    duration = float(value)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("timing durations must be finite and non-negative")
    return duration
