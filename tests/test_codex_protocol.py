from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import officeagent.infrastructure.codex_app_server as codex_module
from officeagent.infrastructure.codex_app_server import (
    AppServerSession,
    CodexAppServerGateway,
    CodexAppServerTimeoutError,
    CodexAppServerToolUseError,
    CodexAppServerTurnError,
    CodexAppServerZeroDeltaError,
)
from officeagent.infrastructure.settings import Settings


@dataclass
class FakeSession(AppServerSession):
    responses: deque[Mapping[str, Any] | Exception]
    notifications: deque[Mapping[str, Any] | Exception]
    requests: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    notifications_sent: list[tuple[str, Mapping[str, Any] | None]] = field(
        default_factory=list
    )
    closed: bool = False

    async def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.requests.append((method, params))
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    async def next_notification(self, *, timeout_seconds: float) -> Mapping[str, Any]:
        del timeout_seconds
        event = self.notifications.popleft()
        if isinstance(event, Exception):
            raise event
        return event

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.notifications_sent.append((method, params))

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeSessionFactory:
    sessions: deque[FakeSession]
    created: list[FakeSession] = field(default_factory=list)

    async def __call__(self) -> AppServerSession:
        session = self.sessions.popleft()
        self.created.append(session)
        return session


def _responses(thread_id: str = "thread-1", turn_id: str = "turn-1") -> deque[Mapping[str, Any]]:
    return deque(
        [
            {"ok": True},
            {"thread": {"id": thread_id}},
            {"turn": {"id": turn_id}},
        ]
    )


def _delta(text: str, *, thread_id: str = "thread-1", turn_id: str = "turn-1") -> Mapping[str, Any]:
    return {
        "method": "item/agentMessage/delta",
        "params": {"threadId": thread_id, "turnId": turn_id, "itemId": "item-1", "delta": text},
    }


def _completed(
    *,
    status: str = "completed",
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
) -> Mapping[str, Any]:
    return {
        "method": "turn/completed",
        "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "items": []}},
    }


async def _collect(gateway: CodexAppServerGateway) -> list[str]:
    return [delta async for delta in gateway.stream_answer("grounded prompt")]


def _gateway(factory: FakeSessionFactory) -> CodexAppServerGateway:
    return CodexAppServerGateway(
        Settings(),
        repository_root=Path("D:/repo"),
        session_factory=factory,
    )


async def test_protocol_order_filters_unrelated_deltas_and_assembles_answer() -> None:
    session = FakeSession(
        responses=_responses(),
        notifications=deque(
            [
                _delta("ignore-thread", thread_id="other"),
                _delta("ignore-turn", turn_id="other"),
                _delta("Hello, "),
                _delta("world"),
                _completed(),
            ]
        ),
    )
    factory = FakeSessionFactory(deque([session]))

    assert await _collect(_gateway(factory)) == ["Hello, ", "world"]
    assert [method for method, _ in session.requests] == [
        "initialize",
        "thread/start",
        "turn/start",
    ]
    assert session.notifications_sent == [("initialized", None)]
    thread_params = session.requests[1][1]
    assert thread_params["ephemeral"] is True
    assert thread_params["sandbox"] == "read-only"
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["environments"] == []
    assert thread_params["dynamicTools"] == []
    assert session.requests[0][1]["capabilities"]["experimentalApi"] is True
    assert session.closed is True


async def test_completed_turn_without_delta_is_rejected() -> None:
    session = FakeSession(responses=_responses(), notifications=deque([_completed()]))
    factory = FakeSessionFactory(deque([session]))

    with pytest.raises(CodexAppServerZeroDeltaError):
        await _collect(_gateway(factory))
    assert len(factory.created) == 1


async def test_failed_turn_is_rejected_without_retry() -> None:
    session = FakeSession(
        responses=_responses(),
        notifications=deque([_completed(status="failed")]),
    )
    factory = FakeSessionFactory(deque([session]))

    with pytest.raises(CodexAppServerTurnError):
        await _collect(_gateway(factory))
    assert len(factory.created) == 1


async def test_timeout_before_first_delta_retries_once_with_new_session() -> None:
    first = FakeSession(
        responses=_responses(),
        notifications=deque([CodexAppServerTimeoutError("no first delta")]),
    )
    second = FakeSession(
        responses=_responses(),
        notifications=deque([_delta("recovered"), _completed()]),
    )
    factory = FakeSessionFactory(deque([first, second]))

    assert await _collect(_gateway(factory)) == ["recovered"]
    assert len(factory.created) == 2
    assert first.closed is True
    assert second.closed is True


async def test_timeout_after_delta_does_not_retry() -> None:
    first = FakeSession(
        responses=_responses(),
        notifications=deque([_delta("partial"), CodexAppServerTimeoutError("stream stopped")]),
    )
    second = FakeSession(
        responses=_responses(),
        notifications=deque([_delta("must not run"), _completed()]),
    )
    factory = FakeSessionFactory(deque([first, second]))

    with pytest.raises(CodexAppServerTimeoutError):
        await _collect(_gateway(factory))
    assert len(factory.created) == 1
    assert first.closed is True


async def test_empty_delta_does_not_block_pre_visible_delta_retry() -> None:
    first = FakeSession(
        responses=_responses(),
        notifications=deque(
            [_delta(""), CodexAppServerTimeoutError("no visible first delta")]
        ),
    )
    second = FakeSession(
        responses=_responses(),
        notifications=deque([_delta("recovered"), _completed()]),
    )
    factory = FakeSessionFactory(deque([first, second]))

    assert await _collect(_gateway(factory)) == ["recovered"]
    assert len(factory.created) == 2


async def test_tool_attempt_is_rejected_without_retry() -> None:
    tool_started = {
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "startedAtMs": 1,
            "item": {"id": "tool-1", "type": "commandExecution"},
        },
    }
    first = FakeSession(
        responses=_responses(),
        notifications=deque([tool_started]),
    )
    factory = FakeSessionFactory(deque([first]))

    with pytest.raises(CodexAppServerToolUseError):
        await _collect(_gateway(factory))
    assert len(factory.created) == 1


async def test_server_action_request_is_rejected_without_retry() -> None:
    action_request = {
        "id": 99,
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "thread-1", "turnId": "turn-1"},
    }
    first = FakeSession(
        responses=_responses(),
        notifications=deque([action_request]),
    )
    factory = FakeSessionFactory(deque([first]))

    with pytest.raises(CodexAppServerToolUseError):
        await _collect(_gateway(factory))
    assert len(factory.created) == 1


async def test_child_process_receives_only_allowlisted_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "CODEX_HOME": "/safe/codex",
        "LANG": "C.UTF-8",
        "TMPDIR": "/safe/tmp",
        "SSL_CERT_FILE": "/safe/cert.pem",
        "HTTPS_PROXY": "http://safe-proxy.invalid",
        "NO_PROXY": "127.0.0.1",
    }
    excluded = {
        "OPENAI_API_KEY": "openai-secret",
        "GITHUB_TOKEN": "github-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "OFFICEAGENT_DATABASE_URL": "postgresql://user:secret@db/officeagent",
    }
    for name, value in {**allowed, **excluded}.items():
        monkeypatch.setenv(name, value)

    captured: dict[str, Any] = {}
    sentinel = object()

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return object()

    def fake_session(
        process: object,
        *,
        notification_queue_size: int,
    ) -> object:
        captured["process"] = process
        captured["notification_queue_size"] = notification_queue_size
        return sentinel

    monkeypatch.setattr(
        codex_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(codex_module, "_SubprocessAppServerSession", fake_session)
    gateway = CodexAppServerGateway(
        Settings(data_dir=tmp_path),
        repository_root=tmp_path,
    )

    session = await gateway._create_subprocess_session()

    assert session is sentinel
    child_environment = captured["env"]
    assert isinstance(child_environment, dict)
    assert {name: child_environment[name] for name in allowed} == allowed
    assert set(child_environment) <= codex_module.CODEX_CHILD_ENV_ALLOWLIST
    assert not set(excluded) & set(child_environment)
