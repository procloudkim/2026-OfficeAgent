"""SSE contract over genuine gateway deltas and cache replay."""

from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from officeagent.api.app import create_app
from tests.helpers import FakeLlmGateway, build_test_runtime


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


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events
