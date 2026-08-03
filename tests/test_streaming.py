"""SSE contract over genuine gateway deltas and cache replay."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from officeagent.api.app import create_app
from officeagent.application.question_answering import normalize_question
from officeagent.infrastructure.codex_app_server import CodexAppServerProtocolError
from tests.helpers import FakeLlmGateway, build_test_runtime


@dataclass
class PartiallyInvalidLlmGateway:
    """Expose one visible delta, then fail the structured-output contract."""

    calls: int = 0

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        del prompt
        self.calls += 1
        yield "partial answer"
        raise CodexAppServerProtocolError(
            "Structured answer object contains additional data"
        )


async def test_sse_emits_ordered_live_deltas_citation_and_cache_metadata(
    test_data_dir,
) -> None:
    llm = FakeLlmGateway(("first ", "second"))
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    app = create_app(runtime.services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.post(
                "/v1/questions/stream",
                json={"question": "Explain the education policy"},
            )
            cached = await client.post(
                "/v1/questions/stream",
                json={"question": "Explain the education policy"},
            )
            whitespace = await client.post(
                "/v1/questions/stream",
                json={"question": "   "},
            )

    first_events = _parse_sse(first.text)
    cached_events = _parse_sse(cached.text)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in first_events] == [
        "answer.started",
        "answer.delta",
        "answer.delta",
        "answer.citation",
        "answer.completed",
    ]
    assert [data["delta"] for name, data in first_events if name == "answer.delta"] == [
        "first ",
        "second",
    ]
    assert first_events[0][1]["cache_hit"] is False
    assert first_events[-1][1]["grounded"] is True
    assert len({data["request_id"] for _, data in first_events}) == 1
    assert cached_events[0][1]["cache_hit"] is True
    assert cached_events[0][1]["cache_match"] == "exact"
    assert whitespace.status_code == 422
    assert len(llm.prompts) == 1


async def test_invalid_structured_output_never_completes_or_populates_cache(
    test_data_dir,
) -> None:
    llm = PartiallyInvalidLlmGateway()
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    app = create_app(runtime.services)
    question = "Explain the education policy"

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            streamed = await client.post(
                "/v1/questions/stream",
                json={"question": question},
            )
            collected = await client.post(
                "/v1/questions",
                json={"question": question},
            )

    streamed_events = _parse_sse(streamed.text)
    assert streamed.status_code == 200
    assert [name for name, _ in streamed_events] == [
        "answer.started",
        "answer.delta",
        "answer.error",
    ]
    assert streamed_events[1][1]["delta"] == "partial answer"
    assert streamed_events[-1][1]["code"] == "answer_generation_failed"
    assert collected.status_code == 502
    assert collected.json() == {"detail": "Answer generation failed"}
    assert llm.calls == 2

    cached = await runtime.metadata.find_cached_answer(
        normalized_question=normalize_question(question),
        question_embedding=await runtime.embeddings.embed_query(question),
        similarity_threshold=0.95,
        cache_versions=runtime.cache_versions,
    )
    assert cached is None


async def test_live_answer_and_cache_replay_preserve_identical_text(
    test_data_dir,
) -> None:
    llm = FakeLlmGateway(("  spaced answer  ",))
    runtime = await build_test_runtime(test_data_dir, llm=llm)
    await runtime.services.ingestion.ingest(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Education policy includes a yearly allowance.",
    )
    app = create_app(runtime.services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            live = await client.post(
                "/v1/questions/stream",
                json={"question": "Explain the education policy"},
            )
            cached = await client.post(
                "/v1/questions/stream",
                json={"question": "Explain the education policy"},
            )

    live_answer = "".join(
        str(data["delta"])
        for name, data in _parse_sse(live.text)
        if name == "answer.delta"
    )
    cached_answer = "".join(
        str(data["delta"])
        for name, data in _parse_sse(cached.text)
        if name == "answer.delta"
    )
    assert live_answer == cached_answer == "  spaced answer  "


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events
