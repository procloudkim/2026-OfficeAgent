"""Grounded question answering with retrieval, caching, and streaming."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator

from officeagent.application.prompts import PROMPT_TEMPLATE_VERSION, build_grounded_prompt
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
    RevisionAwareCorpusStore,
    VectorStore,
)

NO_EVIDENCE_ANSWER = (
    "업로드된 문서에서 이 질문에 답할 충분한 근거를 찾지 못했습니다."
)

RETRIEVAL_PIPELINE_VERSION = "cosine-score-v1"


def build_retrieval_version(
    *,
    limit: int,
    minimum_score: float,
    cache_threshold: float,
) -> str:
    """Fingerprint every setting that changes retrieval or semantic cache meaning."""

    return (
        f"{RETRIEVAL_PIPELINE_VERSION}:limit={limit}:min={minimum_score:.17g}:"
        f"cache={cache_threshold:.17g}"
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
    ) -> None:
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._llm_gateway = llm_gateway
        self._retrieval_limit = retrieval_limit
        self._minimum_relevance_score = minimum_relevance_score
        self._cache_similarity_threshold = cache_similarity_threshold
        self._corpus_store = corpus_store
        self._cache_versions = cache_versions or CacheVersions(
            prompt_version=PROMPT_TEMPLATE_VERSION,
            embedding_version=embedding_provider.embedding_version,
            retrieval_version=build_retrieval_version(
                limit=retrieval_limit,
                minimum_score=minimum_relevance_score,
                cache_threshold=cache_similarity_threshold,
            ),
        )

    async def stream(self, question: str) -> AsyncIterator[AnswerEvent]:
        """Yield transport-neutral events while the answer is generated."""

        cleaned_question = question.strip()
        if not cleaned_question:
            raise ValueError("Question must not be empty")
        normalized = normalize_question(cleaned_question)
        query_embedding = await self._embedding_provider.embed_query(cleaned_question)
        cache_store = self._corpus_store or self._metadata_store
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
            yield _started(cache_hit=True, cache_match=cache_match)
            async for delta in _replay_cached_answer(cached.answer):
                yield AnswerEvent(AnswerEventType.DELTA, {"delta": delta})
            for citation in cached.citations:
                yield _citation_event(citation)
            yield _completed(
                cache_hit=True,
                cache_match=cache_match,
                grounded=bool(cached.citations),
                cache_written=False,
                cache_skip_reason="cache_hit",
            )
            return

        retrieval_revision: int | None = None
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
            yield AnswerEvent(
                AnswerEventType.DELTA,
                {"delta": NO_EVIDENCE_ANSWER},
            )
            yield _completed(
                cache_hit=False,
                cache_match=None,
                grounded=False,
                cache_written=False,
                cache_skip_reason="no_evidence",
            )
            return

        citations = tuple(_to_citation(hit) for hit in relevant_hits)
        prompt = build_grounded_prompt(cleaned_question, relevant_hits)
        answer_parts: list[str] = []
        async for delta in self._llm_gateway.stream_answer(prompt):
            if not delta:
                continue
            answer_parts.append(delta)
            yield AnswerEvent(AnswerEventType.DELTA, {"delta": delta})
        answer = "".join(answer_parts).strip()
        if not answer:
            raise RuntimeError("LLM stream completed without answer text")

        for citation in citations:
            yield _citation_event(citation)
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
                raise RuntimeError("revision-aware retrieval did not return a revision")
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
        yield _completed(
            cache_hit=False,
            cache_match=None,
            grounded=True,
            cache_written=cache_written,
            cache_skip_reason=cache_skip_reason,
        )

    async def answer(self, question: str) -> AnswerResult:
        """Collect the streaming contract for clients that prefer JSON."""

        parts: list[str] = []
        citations: list[Citation] = []
        cache_hit = False
        cache_match: str | None = None
        grounded = False
        cache_written = False
        cache_skip_reason: str | None = None
        async for event in self.stream(question):
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
