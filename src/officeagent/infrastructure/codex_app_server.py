"""Subscription-backed LLM gateway implemented through the Codex App Server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from officeagent.domain.ports import LlmGateway
from officeagent.infrastructure.settings import Settings, get_settings

JsonObject = Mapping[str, Any]

CODEX_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "CODEX_HOME",
        "CURL_CA_BUNDLE",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "NODE_EXTRA_CA_CERTS",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_REQUIRED_CODEX_CHILD_ENVIRONMENT = ("HOME", "PATH")


class CodexAppServerError(RuntimeError):
    """Base error raised by the Codex App Server gateway."""


class CodexAppServerTransportError(CodexAppServerError):
    """The App Server process or its stdio transport became unavailable."""


class CodexAppServerProtocolError(CodexAppServerError):
    """The App Server returned an invalid or rejected protocol message."""


class CodexAppServerTimeoutError(CodexAppServerError):
    """A bounded App Server operation did not finish in time."""


class CodexAppServerTurnError(CodexAppServerError):
    """An App Server turn ended without a successful completed status."""


class CodexAppServerZeroDeltaError(CodexAppServerTurnError):
    """A completed turn emitted no real assistant-message delta."""


class CodexAppServerToolUseError(CodexAppServerTurnError):
    """The supposedly text-only answer turn attempted to invoke a tool."""


class AppServerSession(Protocol):
    """Minimal JSON-RPC transport used by the gateway and its subscription-free tests."""

    async def request(
        self,
        method: str,
        params: JsonObject,
        *,
        timeout_seconds: float,
    ) -> JsonObject:
        """Send a request and return its matching JSON-RPC result."""

    async def next_notification(self, *, timeout_seconds: float) -> JsonObject:
        """Return the next JSON-RPC notification from the App Server."""

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        """Send a JSON-RPC notification without waiting for a response."""

    async def aclose(self) -> None:
        """Release the underlying transport and child process."""


SessionFactory = Callable[[], Awaitable[AppServerSession]]


class _SubprocessAppServerSession:
    """JSON-line transport around one short-lived ``codex app-server`` process."""

    _EOF = object()

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        notification_queue_size: int,
    ) -> None:
        self._process = process
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[JsonObject]] = {}
        self._notifications: asyncio.Queue[JsonObject | object] = asyncio.Queue(
            maxsize=notification_queue_size
        )
        self._reader_error: Exception | None = None
        self._stderr_tail: list[str] = []
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def request(
        self,
        method: str,
        params: JsonObject,
        *,
        timeout_seconds: float,
    ) -> JsonObject:
        if self._reader_error is not None:
            raise self._reader_error
        if self._process.stdin is None:
            raise CodexAppServerTransportError("Codex App Server stdin is unavailable")

        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = {"id": request_id, "method": method, "params": params}
        try:
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            await self._process.stdin.drain()
            return await asyncio.wait_for(future, timeout_seconds)
        except TimeoutError as error:
            raise CodexAppServerTimeoutError(
                f"Codex App Server {method} timed out after {timeout_seconds:.1f}s"
            ) from error
        except (BrokenPipeError, ConnectionError) as error:
            raise CodexAppServerTransportError(
                "Codex App Server stdin closed unexpectedly"
            ) from error
        finally:
            self._pending.pop(request_id, None)

    async def next_notification(self, *, timeout_seconds: float) -> JsonObject:
        try:
            message = await asyncio.wait_for(self._notifications.get(), timeout_seconds)
        except TimeoutError as error:
            raise CodexAppServerTimeoutError(
                f"Codex App Server emitted no event for {timeout_seconds:.1f}s"
            ) from error
        if message is self._EOF:
            raise self._reader_error or CodexAppServerTransportError(
                "Codex App Server stdout closed unexpectedly"
            )
        return message

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        if self._reader_error is not None:
            raise self._reader_error
        if self._process.stdin is None:
            raise CodexAppServerTransportError("Codex App Server stdin is unavailable")
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        try:
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionError) as error:
            raise CodexAppServerTransportError(
                "Codex App Server stdin closed unexpectedly"
            ) from error

    async def aclose(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.is_closing():
            self._process.stdin.close()
            with contextlib.suppress(Exception):
                await self._process.stdin.wait_closed()

        if self._process.returncode is None:
            self._process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
        if self._process.returncode is None:
            self._process.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), timeout=5.0)

        for task in (self._reader_task, self._stderr_task):
            task.cancel()
        for task in (self._reader_task, self._stderr_task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _read_stdout(self) -> None:
        if self._process.stdout is None:
            self._reader_error = CodexAppServerTransportError(
                "Codex App Server stdout is unavailable"
            )
            await self._notifications.put(self._EOF)
            return
        try:
            while raw_line := await self._process.stdout.readline():
                try:
                    message = json.loads(raw_line)
                except json.JSONDecodeError as error:
                    raise CodexAppServerProtocolError(
                        "Codex App Server emitted invalid JSON"
                    ) from error
                if not isinstance(message, dict):
                    raise CodexAppServerProtocolError(
                        "Codex App Server emitted a non-object message"
                    )
                if "id" in message and ("result" in message or "error" in message):
                    self._resolve_response(message)
                elif "method" in message:
                    await self._notifications.put(message)
        except Exception as error:
            self._reader_error = error
        finally:
            error = self._reader_error or CodexAppServerTransportError(
                "Codex App Server stdout closed unexpectedly"
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            await self._notifications.put(self._EOF)

    def _resolve_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        if "error" in message:
            future.set_exception(CodexAppServerProtocolError(str(message["error"])))
            return
        result = message.get("result")
        if not isinstance(result, dict):
            future.set_exception(
                CodexAppServerProtocolError("Codex App Server response lacks object result")
            )
            return
        future.set_result(result)

    async def _drain_stderr(self) -> None:
        if self._process.stderr is None:
            return
        while raw_line := await self._process.stderr.readline():
            self._stderr_tail.append(raw_line.decode(errors="replace").rstrip())
            if len(self._stderr_tail) > 50:
                self._stderr_tail.pop(0)


class CodexAppServerGateway(LlmGateway):
    """Yield genuine App Server assistant deltas through the domain LLM port."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository_root: Path | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository_root = (repository_root or Path.cwd()).resolve()
        self._session_factory = session_factory or self._create_subprocess_session
        self._concurrency_limit = asyncio.Semaphore(
            self._settings.codex_max_concurrent_requests
        )

    async def stream_answer(self, prompt: str) -> AsyncIterator[str]:
        """Yield only matching real model deltas and never synthesize a stream."""

        async with self._concurrency_limit:
            emitted_delta = False
            for attempt in range(2):
                session: AppServerSession | None = None
                try:
                    session = await self._session_factory()
                    async for delta in self._stream_once(session, prompt):
                        emitted_delta = True
                        yield delta
                    return
                except FileNotFoundError as error:
                    raise CodexAppServerTransportError(
                        f"Codex executable was not found: {self._settings.codex_executable}"
                    ) from error
                except OSError as error:
                    if attempt == 1:
                        raise CodexAppServerTransportError(
                            "Codex App Server process could not be started"
                        ) from error
                except (CodexAppServerTimeoutError, CodexAppServerTransportError):
                    if emitted_delta or attempt == 1:
                        raise
                finally:
                    if session is not None:
                        await session.aclose()

    async def _stream_once(
        self,
        session: AppServerSession,
        prompt: str,
    ) -> AsyncIterator[str]:
        await session.request(
            "initialize",
            {
                "clientInfo": {"name": "officeagent", "version": "0.1.0"},
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [],
                },
            },
            timeout_seconds=self._settings.codex_initialize_timeout_seconds,
        )
        await session.notify("initialized")
        thread_result = await session.request(
            "thread/start",
            {
                "cwd": str(self._repository_root),
                "ephemeral": True,
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "environments": [],
                "dynamicTools": [],
                "baseInstructions": (
                    "Answer only from the supplied prompt and context. Do not call tools, "
                    "read files, browse, or request approval."
                ),
            },
            timeout_seconds=self._settings.codex_thread_timeout_seconds,
        )
        thread_id = _required_id(thread_result, "thread")

        started_at = time.monotonic()
        turn_result = await session.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "approvalPolicy": "never",
                "environments": [],
            },
            timeout_seconds=min(
                self._settings.codex_first_delta_timeout_seconds,
                self._settings.codex_overall_timeout_seconds,
            ),
        )
        turn_id = _required_id(turn_result, "turn")
        saw_delta = False

        while True:
            remaining_overall = self._settings.codex_overall_timeout_seconds - (
                time.monotonic() - started_at
            )
            if remaining_overall <= 0:
                raise CodexAppServerTimeoutError(
                    "Codex App Server turn exceeded its overall timeout"
                )
            remaining = remaining_overall
            if not saw_delta:
                elapsed = time.monotonic() - started_at
                remaining = min(
                    remaining,
                    self._settings.codex_first_delta_timeout_seconds - elapsed,
                )
                if remaining <= 0:
                    raise CodexAppServerTimeoutError(
                        "Codex App Server emitted no first delta in time"
                    )

            message = await session.next_notification(timeout_seconds=remaining)
            if "id" in message and "method" in message:
                raise CodexAppServerToolUseError(
                    f"Codex answer turn requested client action {message.get('method')!r}"
                )
            params = message.get("params")
            if not isinstance(params, Mapping) or params.get("threadId") != thread_id:
                continue
            if message.get("method") == "item/started" and params.get("turnId") == turn_id:
                item = params.get("item")
                if isinstance(item, Mapping) and item.get("type") in _FORBIDDEN_ITEM_TYPES:
                    raise CodexAppServerToolUseError(
                        f"Codex answer turn attempted forbidden item type {item.get('type')!r}"
                    )
            if message.get("method") == "item/agentMessage/delta":
                if params.get("turnId") != turn_id:
                    continue
                delta = params.get("delta")
                if not isinstance(delta, str):
                    raise CodexAppServerProtocolError("Agent-message delta is not text")
                if not delta:
                    continue
                saw_delta = True
                yield delta
                continue
            if message.get("method") == "turn/completed":
                turn = params.get("turn")
                if not isinstance(turn, Mapping) or turn.get("id") != turn_id:
                    continue
                if turn.get("status") != "completed":
                    raise CodexAppServerTurnError(
                        f"Codex App Server turn ended as {turn.get('status')!r}"
                    )
                if not saw_delta:
                    raise CodexAppServerZeroDeltaError(
                        "Completed Codex turn emitted no assistant delta"
                    )
                return

    async def _create_subprocess_session(self) -> AppServerSession:
        process = await asyncio.create_subprocess_exec(
            self._settings.codex_executable,
            "app-server",
            "--listen",
            "stdio://",
            cwd=str(self._repository_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=codex_child_environment(),
        )
        return _SubprocessAppServerSession(
            process,
            notification_queue_size=self._settings.codex_notification_queue_size,
        )


def codex_child_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy only runtime plumbing needed by the subscription-backed Codex CLI."""

    active = os.environ if source is None else source
    child_environment = {
        name: active[name] for name in CODEX_CHILD_ENV_ALLOWLIST if name in active
    }
    missing = [
        name for name in _REQUIRED_CODEX_CHILD_ENVIRONMENT if not child_environment.get(name)
    ]
    if missing:
        raise CodexAppServerTransportError(
            "Codex child environment is missing required variables: " + ", ".join(missing)
        )
    return child_environment


def _required_id(result: JsonObject, object_name: str) -> str:
    value = result.get(object_name)
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
        raise CodexAppServerProtocolError(
            f"Codex App Server {object_name}/start response lacks an id"
        )
    return value["id"]


_FORBIDDEN_ITEM_TYPES = frozenset(
    {
        "collabAgentToolCall",
        "commandExecution",
        "dynamicToolCall",
        "fileChange",
        "imageGeneration",
        "imageView",
        "mcpToolCall",
        "webSearch",
    }
)
