"""Run a real FastEmbed + Codex + Uvicorn HTTP/SSE pipeline smoke.

This is intentionally separate from the subscription-free automated suite.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import httpx
import uvicorn

from officeagent.api.app import create_app
from officeagent.bootstrap import build_services
from officeagent.infrastructure.settings import Settings


async def main() -> None:
    """Upload through HTTP and observe live Codex deltas through a real SSE socket."""

    data_dir = Path(".data") / f"live-smoke-{uuid4().hex}"
    settings = Settings(data_dir=data_dir)
    services = await build_services(settings)
    app = create_app(services)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="on")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))

    try:
        await _wait_until_started(server)
        document_path = Path("sample-docs/company-policy.txt")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=settings.codex_overall_timeout_seconds + 30,
        ) as client:
            upload = await client.post(
                "/v1/documents",
                files={
                    "file": (
                        document_path.name,
                        await anyio.to_thread.run_sync(document_path.read_bytes),
                        "text/plain",
                    )
                },
            )
            upload.raise_for_status()
            events = await _collect_sse(
                client,
                question="직무 관련 교육비의 연간 지원 한도는?",
            )
    finally:
        server.should_exit = True
        await server_task
        listener.close()

    deltas = [
        str(data["delta"]) for name, data in events if name == "answer.delta"
    ]
    citations = [
        str(data["filename"]) for name, data in events if name == "answer.citation"
    ]
    event_names = [name for name, _ in events]
    if len(deltas) < 2:
        raise RuntimeError(f"Expected at least two HTTP SSE deltas, received {len(deltas)}")
    if "answer.completed" not in event_names or document_path.name not in citations:
        raise RuntimeError("Live HTTP answer did not complete with the uploaded citation")
    if event_names.index("answer.delta") > event_names.index("answer.completed"):
        raise RuntimeError("First answer delta arrived after completion")

    request_ids = {str(data["request_id"]) for _, data in events}
    if len(request_ids) != 1:
        raise RuntimeError("SSE events did not share one request_id")

    print(
        json.dumps(
            {
                "status": "ok",
                "transport": "uvicorn-http-sse",
                "document_id": upload.json()["document_id"],
                "document_version": upload.json()["document_version"],
                "delta_count": len(deltas),
                "first_delta_before_completed": True,
                "answer": "".join(deltas),
                "citations": citations,
                "data_dir": str(data_dir),
            },
            ensure_ascii=False,
        )
    )


async def _wait_until_started(server: uvicorn.Server) -> None:
    for _ in range(1_000):
        if server.started:
            return
        if server.should_exit:
            break
        await asyncio.sleep(0.01)
    raise RuntimeError("Uvicorn did not start within 10 seconds")


async def _collect_sse(
    client: httpx.AsyncClient,
    *,
    question: str,
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    current_event: str | None = None
    async with client.stream(
        "POST",
        "/v1/questions/stream",
        json={"question": question},
    ) as response:
        response.raise_for_status()
        if not response.headers["content-type"].startswith("text/event-stream"):
            raise RuntimeError("Streaming endpoint did not return text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ")
            elif line.startswith("data: ") and current_event is not None:
                payload = json.loads(line.removeprefix("data: "))
                events.append((current_event, payload))
                if current_event == "answer.error":
                    raise RuntimeError(f"Live SSE returned answer.error: {payload}")
                current_event = None
    return events


if __name__ == "__main__":
    asyncio.run(main())
