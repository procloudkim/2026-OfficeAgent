"""Grounded question answering with retrieval, caching, and streaming."""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections.abc import AsyncIterator, Callable

from officeagent.application.chunking import LEGACY_CHARACTER_CHUNKING_VERSION
from officeagent.application.observability import (
    AnswerPath,
    PipelineOperation,
    PipelineOutcome,
    PipelineTimer,
    TimingRecorder,
    TimingStage,
    outcome_for_exception,
)
from officeagent.application.prompts import (
    PROMPT_TEMPLATE_VERSION,
    build_grounded_prompt,
    build_prompt_cache_version,
    pack_grounded_prompt,
)
from officeagent.domain.models import (
    AnswerEvent,
    AnswerEventType,
    AnswerResult,
    CacheVersions,
    Citation,
    SearchHit,
)
from officeagent.domain.ports import (
    EmbeddingProvider,
    LlmGateway,
    MetadataStore,
    PromptTokenCounter,
    RevisionAwareCorpusStore,
    VectorStore,
)

NO_EVIDENCE_ANSWER = (
    "업로드된 문서에서 이 질문에 답할 충분한 근거를 찾지 못했습니다."
)
PROMPT_BUDGET_ANSWER = (
    "검색된 근거를 안전한 프롬프트 크기 안에 담을 수 없어 답변을 생성하지 않았습니다."
)

RETRIEVAL_PIPELINE_VERSION = "cosine-score-v1"


def build_retrieval_version(
    *,
    limit: int,
    minimum_score: float,
    cache_threshold: float,
    chunking_version: str = LEGACY_CHARACTER_CHUNKING_VERSION,
) -> str:
    """Fingerprint every setting that changes retrieval or semantic cache meaning."""

    if not chunking_version.strip():
        raise ValueError("chunking_version must not be blank")
    chunking_digest = hashlib.sha256(chunking_version.encode("utf-8")).hexdigest()
    return (
        f"{RETRIEVAL_PIPELINE_VERSION}:limit={limit}:min={minimum_score:.17g}:"
        f"cache={cache_threshold:.17g}:chunking={chunking_digest}"
    )


class QuestionAnsweringService:
    """Coordinate cache lookup, vector retrieval, LLM deltas, and citations."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        llm_gateway: LlmGateway,
        *,
        retrieval_limit: int,
        minimum_relevance_score: float,
        cache_similarity_threshold: float,
        cache_versions: CacheVersions | None = None,
        corpus_store: RevisionAwareCorpusStore | None = None,
        token_counter: PromptTokenCounter | None = None,
        prompt_input_token_budget: int | None = None,
        timing_recorder: TimingRecorder | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        if (token_counter is None) != (prompt_input_token_budget is None):
            raise ValueError(
                "token_counter and prompt_input_token_budget must be configured together"
            )
        if prompt_input_token_budget is not None and prompt_input_token_budget < 1:
            raise ValueError("prompt_input_token_budget must be positive")
        prompt_version = PROMPT_TEMPLATE_VERSION
        if token_counter is not None and prompt_input_token_budget is not None:
            prompt_version = build_prompt_cache_version(
                token_counter_version=token_counter.token_counter_version,
                input_token_budget=prompt_input_token_budget,
            )
        if cache_versions is not None and cache_versions.prompt_version != prompt_version:
            raise ValueError(
                "cache prompt version does not match the configured prompt packing policy"
            )
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._llm_gateway = llm_gateway
        self._retrieval_limit = retrieval_limit
        self._minimum_relevance_score = minimum_relevance_score
        self._cache_similarity_threshold = cache_similarity_threshold
        self._corpus_store = corpus_store
        self._token_counter = token_counter
        self._prompt_input_token_budget = prompt_input_token_budget
        self._timing_recorder = timing_recorder
        self._clock = clock
        self._cache_versions = cache_versions or CacheVersions(
            prompt_version=prompt_version,
            embedding_version=embedding_provider.embedding_version,
            retrieval_version=build_retrieval_version(
                limit=retrieval_limit,
                minimum_score=minimum_relevance_score,
                cache_threshold=cache_similarity_threshold,
                chunking_version=LEGACY_CHARACTER_CHUNKING_VERSION,
            ),
        )

    async def stream(
        self,
        question: str,
        *,
        request_id: str | None = None,
    ) -> AsyncIterator[AnswerEvent]:
        """Yield transport-neutral events while the answer is generated."""

        timer = PipelineTimer(
            PipelineOperation.QUESTION_ANSWERING,
            request_id=request_id,
            recorder=self._timing_recorder,
            clock=self._clock,
        )
        observed_cache_hit: bool | None = None
        observed_cache_match: str | None = None
        observed_answer_path: AnswerPath | None = None
        try:
            cleaned_question = question.strip()
            if not cleaned_question:
                raise ValueError("Question must not be empty")
            normalized = normalize_question(cleaned_question)
            with timer.measure(TimingStage.EMBED):
                query_embedding = await self._embedding_provider.embed_query(
                    cleaned_question
                )
            cache_store = self._corpus_store or self._metadata_store
            with timer.measure(TimingStage.CACHE_LOOKUP):
                cached = await cache_store.find_cached_answer(
                    normalized_question=normalized,
                    question_embedding=query_embedding,
                    similarity_threshold=self._cache_similarity_threshold,
                    cache_versions=self._cache_versions,
                )
            if cached is not None:
                cache_match = (
                    "exact" if cached.normalized_question == normalized else "similar"
                )
                observed_cache_hit = True
                observed_cache_match = cache_match
                observed_answer_path = AnswerPath.CACHE_HIT
                yield _started(cache_hit=True, cache_match=cache_match)
                async for delta in _replay_cached_answer(cached.answer):
                    yield AnswerEvent(AnswerEventType.DELTA, {"delta": delta})
                for citation in cached.citations:
                    yield _citation_event(citation)
                timer.finish(
                    PipelineOutcome.COMPLETED,
                    cache_hit=observed_cache_hit,
                    cache_match=observed_cache_match,
                    answer_path=observed_answer_path,
                )
                yield _completed(
                    cache_hit=True,
                    cache_match=cache_match,
                    grounded=bool(cached.citations),
                    cache_written=False,
                    cache_skip_reason="cache_hit",
                )
                return

            observed_cache_hit = False
            retrieval_revision: int | None = None
            with timer.measure(TimingStage.RETRIEVE):
                if self._corpus_store is None:
                    hits = await self._vector_store.search(
                        query_embedding,
                        limit=self._retrieval_limit,
                    )
                else:
                    snapshot = await self._corpus_store.retrieve(
                        query_embedding,
                        limit=self._retrieval_limit,
                    )
                    hits = list(snapshot.hits)
                    retrieval_revision = snapshot.revision
            relevant_hits = [
                hit for hit in hits if hit.score >= self._minimum_relevance_score
            ]
            yield _started(cache_hit=False, cache_match=None)
            if not relevant_hits:
                observed_answer_path = AnswerPath.NO_EVIDENCE
                yield AnswerEvent(
                    AnswerEventType.DELTA,
                    {"delta": NO_EVIDENCE_ANSWER},
                )
                timer.finish(
                    PipelineOutcome.COMPLETED,
                    cache_hit=observed_cache_hit,
                    answer_path=observed_answer_path,
                )
                yield _completed(
                    cache_hit=False,
                    cache_match=None,
                    grounded=False,
                    cache_written=False,
                    cache_skip_reason="no_evidence",
                )
                return

            selected_hits = tuple(relevant_hits)
            prompt_evidence_reduced = False
            if (
                self._token_counter is not None
                and self._prompt_input_token_budget is not None
            ):
                packed = await pack_grounded_prompt(
                    cleaned_question,
                    relevant_hits,
                    token_counter=self._token_counter,
                    input_token_budget=self._prompt_input_token_budget,
                    prompt_version=self._cache_versions.prompt_version,
                )
                if packed.prompt is None:
                    observed_answer_path = AnswerPath.PROMPT_BUDGET
                    yield AnswerEvent(
                        AnswerEventType.DELTA,
                        {"delta": PROMPT_BUDGET_ANSWER},
                    )
                    timer.finish(
                        PipelineOutcome.COMPLETED,
                        cache_hit=observed_cache_hit,
                        answer_path=observed_answer_path,
                    )
                    yield _completed(
                        cache_hit=False,
                        cache_match=None,
                        grounded=False,
                        cache_written=False,
                        cache_skip_reason="prompt_budget_exceeded",
                    )
                    return
                prompt = packed.prompt
                selected_hits = packed.hits
                prompt_evidence_reduced = len(selected_hits) != len(relevant_hits)
            else:
                prompt = build_grounded_prompt(cleaned_question, selected_hits)

            observed_answer_path = AnswerPath.GROUNDED
            citations = tuple(_to_citation(hit) for hit in selected_hits)
            answer_parts: list[str] = []
            llm_deltas = self._llm_gateway.stream_answer(prompt).__aiter__()
            try:
                while True:
                    try:
                        with timer.measure(TimingStage.LLM):
                            delta = await anext(llm_deltas)
                    except StopAsyncIteration:
                        break
                    if not delta:
                        continue
                    answer_parts.append(delta)
                    yield AnswerEvent(AnswerEventType.DELTA, {"delta": delta})
            finally:
                close = getattr(llm_deltas, "aclose", None)
                if close is not None:
                    await close()
            answer = "".join(answer_parts)
            if not answer.strip():
                raise RuntimeError("LLM stream completed without answer text")

            for citation in citations:
                yield _citation_event(citation)
            if prompt_evidence_reduced:
                cache_written = False
                cache_skip_reason = "prompt_evidence_reduced"
            else:
                with timer.measure(TimingStage.CACHE_WRITE):
                    if self._corpus_store is None:
                        await self._metadata_store.save_cached_answer(
                            normalized_question=normalized,
                            question_embedding=query_embedding,
                            answer=answer,
                            citations=citations,
                            cache_versions=self._cache_versions,
                        )
                        cache_written = True
                        cache_skip_reason = None
                    else:
                        if retrieval_revision is None:
                            raise RuntimeError(
                                "revision-aware retrieval did not return a revision"
                            )
                        write_result = await self._corpus_store.save_cached_answer(
                            normalized_question=normalized,
                            question_embedding=query_embedding,
                            answer=answer,
                            citations=citations,
                            cache_versions=self._cache_versions,
                            expected_revision=retrieval_revision,
                        )
                        cache_written = write_result.written
                        cache_skip_reason = write_result.skip_reason
            timer.finish(
                PipelineOutcome.COMPLETED,
                cache_hit=observed_cache_hit,
                answer_path=observed_answer_path,
            )
            yield _completed(
                cache_hit=False,
                cache_match=None,
                grounded=True,
                cache_written=cache_written,
                cache_skip_reason=cache_skip_reason,
            )
        except BaseException as exc:
            if not timer.finished:
                timer.finish(
                    outcome_for_exception(exc),
                    cache_hit=observed_cache_hit,
                    cache_match=observed_cache_match,
                    answer_path=observed_answer_path,
                    error_type=type(exc).__name__,
                )
            raise

    async def answer(
        self,
        question: str,
        *,
        request_id: str | None = None,
    ) -> AnswerResult:
        """Collect the streaming contract for clients that prefer JSON."""

        parts: list[str] = []
        citations: list[Citation] = []
        cache_hit = False
        cache_match: str | None = None
        grounded = False
        cache_written = False
        cache_skip_reason: str | None = None
        async for event in self.stream(question, request_id=request_id):
            if event.type is AnswerEventType.STARTED:
                cache_hit = bool(event.payload["cache_hit"])
                raw_match = event.payload.get("cache_match")
                cache_match = str(raw_match) if raw_match is not None else None
            elif event.type is AnswerEventType.DELTA:
                parts.append(str(event.payload["delta"]))
            elif event.type is AnswerEventType.CITATION:
                citations.append(_citation_from_payload(event.payload))
            elif event.type is AnswerEventType.COMPLETED:
                grounded = bool(event.payload["grounded"])
                cache_written = bool(event.payload["cache_written"])
                raw_skip_reason = event.payload.get("cache_skip_reason")
                cache_skip_reason = (
                    str(raw_skip_reason) if raw_skip_reason is not None else None
                )
        return AnswerResult(
            answer="".join(parts),
            citations=tuple(citations),
            cache_hit=cache_hit,
            cache_match=cache_match,
            grounded=grounded,
            cache_written=cache_written,
            cache_skip_reason=cache_skip_reason,
        )


def normalize_question(question: str) -> str:
    """Normalize harmless lexical variation for exact-cache lookup."""

    normalized = unicodedata.normalize("NFKC", question).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _to_citation(hit: SearchHit) -> Citation:
    chunk = hit.chunk
    return Citation(
        document_id=chunk.document_id,
        document_version=chunk.document_version,
        filename=chunk.filename,
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        page_number=chunk.page_number,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        quote=chunk.text[:400],
        score=hit.score,
    )


def _started(*, cache_hit: bool, cache_match: str | None) -> AnswerEvent:
    return AnswerEvent(
        AnswerEventType.STARTED,
        {"cache_hit": cache_hit, "cache_match": cache_match},
    )


def _completed(
    *,
    cache_hit: bool,
    cache_match: str | None,
    grounded: bool,
    cache_written: bool,
    cache_skip_reason: str | None,
) -> AnswerEvent:
    return AnswerEvent(
        AnswerEventType.COMPLETED,
        {
            "cache_hit": cache_hit,
            "cache_match": cache_match,
            "grounded": grounded,
            "cache_written": cache_written,
            "cache_skip_reason": cache_skip_reason,
        },
    )


def _citation_event(citation: Citation) -> AnswerEvent:
    return AnswerEvent(
        AnswerEventType.CITATION,
        {
            "document_id": citation.document_id,
            "document_version": citation.document_version,
            "filename": citation.filename,
            "chunk_id": citation.chunk_id,
            "chunk_index": citation.chunk_index,
            "page_number": citation.page_number,
            "start_offset": citation.start_offset,
            "end_offset": citation.end_offset,
            "quote": citation.quote,
            "score": citation.score,
        },
    )


def _citation_from_payload(payload: dict[str, object]) -> Citation:
    page_number = payload.get("page_number")
    return Citation(
        document_id=str(payload["document_id"]),
        document_version=int(payload["document_version"]),
        filename=str(payload["filename"]),
        chunk_id=str(payload["chunk_id"]),
        chunk_index=int(payload["chunk_index"]),
        page_number=int(page_number) if page_number is not None else None,
        start_offset=int(payload["start_offset"]),
        end_offset=int(payload["end_offset"]),
        quote=str(payload["quote"]),
        score=float(payload["score"]),
    )


async def _replay_cached_answer(answer: str) -> AsyncIterator[str]:
    """Replay without pretending cached bytes came from the live model."""

    chunk_size = 80
    for start in range(0, len(answer), chunk_size):
        yield answer[start : start + chunk_size]
