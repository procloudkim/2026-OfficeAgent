"""Run a real FastEmbed + Codex + Uvicorn HTTP/SSE pipeline smoke.

This is intentionally separate from the subscription-free automated suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import anyio
import httpx
import psycopg
import uvicorn
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from officeagent.api.app import create_app
from officeagent.bootstrap import build_services
from officeagent.infrastructure.codex_app_server import codex_child_environment
from officeagent.infrastructure.settings import Settings
from officeagent.infrastructure.test_runner import (
    MAX_DATABASE_CONNINFO_LENGTH,
    temporary_database,
    validate_isolated_database_url,
)
from scripts.live_smoke_contract import (
    build_live_receipt,
    build_readiness_receipt,
    parse_codex_version,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ADMIN_DATABASE_NAME = "postgres"
_CODEX_VERSION_TIMEOUT_SECONDS = 5.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LIVE_CODEX_WORKSPACE_DIRECTORY_NAME = "codex-workspace"
_RUNNER_ENV_FIELD_ALLOWLIST = {
    "OFFICEAGENT_DATABASE_URL": "database_url",
    "OFFICEAGENT_DATABASE_ADMIN_URL": "database_admin_url",
}
_READINESS_HTTP_OVERHEAD_SECONDS = 5.0
_SERVER_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_UPDATE_MARKER = "\n\n[Live smoke revision]\n검증용 문서 revision=2\n".encode()


async def main(argv: Sequence[str] | None = None) -> None:
    """Run the bounded live contract and print only after every cleanup succeeds."""

    readiness_only = _parse_args(argv).readiness_only
    base_settings = _runner_settings()
    with _isolated_data_directory() as data_dir:
        if readiness_only:
            with _disposable_live_settings(base_settings, data_dir) as live_settings:
                receipt = await _run_readiness_smoke(live_settings)
        else:
            codex_version = await _read_codex_version(
                base_settings.codex_executable,
                cwd=data_dir,
            )
            with _disposable_live_settings(base_settings, data_dir) as live_settings:
                receipt = await _run_live_smoke(
                    live_settings,
                    codex_version=codex_version,
                )
    separators = (",", ":") if readiness_only else None
    print(json.dumps(receipt, ensure_ascii=False, separators=separators))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readiness-only",
        action="store_true",
        help="verify disposable PostgreSQL readiness without Codex or documents",
    )
    return parser.parse_args(argv)


def _runner_settings(environment: Mapping[str, str] | None = None) -> Settings:
    """Load canonical runner defaults plus only the approved database overrides."""

    active_environment = os.environ if environment is None else environment
    values = {
        name: field.get_default(call_default_factory=True)
        for name, field in Settings.model_fields.items()
    }
    values.update(
        {
            field_name: active_environment[environment_name]
            for environment_name, field_name in _RUNNER_ENV_FIELD_ALLOWLIST.items()
            if environment_name in active_environment
        }
    )
    return Settings(**values, _env_file=None)


async def _run_live_smoke(
    settings: Settings,
    *,
    codex_version: str,
) -> dict[str, Any]:
    """Exercise readiness and the complete live G7 transition over one TCP API."""

    async with _running_server(settings) as port:
        document_path = _sample_document_path()
        document_content = await anyio.to_thread.run_sync(document_path.read_bytes)
        async with _http_client(settings, port) as client:
            readiness_response = await client.get("/health/ready")
            readiness_response.raise_for_status()
            created_upload = await _upload_document(
                client,
                document_name=document_path.name,
                content=document_content,
            )
            initial_events = await _collect_sse(client, question=_question())
            cached_events = await _collect_sse(client, question=_question())
            updated_upload = await _upload_document(
                client,
                document_name=document_path.name,
                content=_updated_document_content(document_content),
            )
            updated_events = await _collect_sse(client, question=_question())

    return build_live_receipt(
        readiness=_json_object(readiness_response),
        created_upload=created_upload,
        updated_upload=updated_upload,
        initial_events=initial_events,
        cached_events=cached_events,
        updated_events=updated_events,
        document_name=document_path.name,
        codex_version=codex_version,
    )


async def _run_readiness_smoke(settings: Settings) -> dict[str, Any]:
    """Exercise only the real Uvicorn readiness endpoint over a disposable database."""

    async with _running_server(settings) as port:
        async with _http_client(
            settings,
            port,
            timeout_seconds=(
                settings.readiness_timeout_seconds + _READINESS_HTTP_OVERHEAD_SECONDS
            ),
        ) as client:
            response = await client.get("/health/ready")
            response.raise_for_status()
            readiness = _json_object(response)
    return build_readiness_receipt(
        http_status=response.status_code,
        readiness=readiness,
    )


@asynccontextmanager
async def _running_server(settings: Settings) -> AsyncIterator[int]:
    """Start the product ASGI app on loopback and always stop its Uvicorn server."""

    validate_isolated_database_url(settings.database_url)
    services = await build_services(settings)
    app = create_app(services)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await _wait_until_started(server, server_task)
            yield port
        finally:
            await _stop_server(server, server_task)


async def _stop_server(server: uvicorn.Server, server_task: asyncio.Task[None]) -> None:
    """Bound Uvicorn shutdown so cleanup cannot wait forever."""

    server.should_exit = True
    try:
        await asyncio.wait_for(server_task, timeout=_SERVER_SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        server_task.cancel()
        with suppress(asyncio.CancelledError):
            await server_task
        raise RuntimeError("Uvicorn did not stop within 10 seconds") from None


async def _upload_document(
    client: httpx.AsyncClient,
    *,
    document_name: str,
    content: bytes,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/documents",
        files={"file": (document_name, content, "text/plain")},
    )
    response.raise_for_status()
    return _json_object(response)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Live HTTP endpoint returned a non-object JSON response")
    return payload


def _http_client(
    settings: Settings,
    port: int,
    *,
    timeout_seconds: float | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=timeout_seconds or settings.codex_overall_timeout_seconds + 30,
        trust_env=False,
    )


def _sample_document_path() -> Path:
    """Resolve the fixture independently from the caller's working directory."""

    return PROJECT_ROOT / "sample-docs/company-policy.txt"


def _question() -> str:
    return "직무 관련 교육비의 연간 지원 한도는?"


def _updated_document_content(content: bytes) -> bytes:
    if _UPDATE_MARKER in content:
        raise RuntimeError("Live smoke fixture already contains the update marker")
    return content + _UPDATE_MARKER


@contextmanager
def _isolated_data_directory() -> Iterator[Path]:
    """Create and remove a workspace that cannot sit inside the private repository."""

    with TemporaryDirectory(prefix="officeagent-live-smoke-") as directory:
        data_dir = Path(directory).resolve()
        if data_dir.is_relative_to(PROJECT_ROOT):
            raise RuntimeError("Live smoke temporary data directory must be outside the project")
        yield data_dir


@contextmanager
def _disposable_live_settings(
    base_settings: Settings,
    data_dir: Path,
) -> Iterator[Settings]:
    """Create a local disposable database and never expose the product target to services."""

    data_root = data_dir.resolve()
    workspace_name = _validated_live_workspace_name(data_root)
    admin_url = _resolve_admin_database_url(
        base_settings.database_url,
        base_settings.database_admin_url,
    )
    try:
        with temporary_database(admin_url) as database_url:
            validate_isolated_database_url(database_url)
            live_settings = base_settings.model_copy(
                update={
                    "data_dir": data_root,
                    "database_url": database_url,
                    "database_admin_url": None,
                    "codex_workspace_directory_name": workspace_name,
                }
            )
            if not live_settings.codex_workspace_path.resolve().is_relative_to(data_root):
                raise RuntimeError("Live smoke Codex workspace escaped the disposable directory")
            yield live_settings
    except psycopg.Error:
        raise RuntimeError("Live smoke disposable database lifecycle failed") from None


def _validated_live_workspace_name(data_root: Path) -> str:
    workspace_name = _LIVE_CODEX_WORKSPACE_DIRECTORY_NAME
    workspace_path = (data_root / workspace_name).resolve()
    if (
        Path(workspace_name).name != workspace_name
        or workspace_path == data_root
        or not workspace_path.is_relative_to(data_root)
    ):
        raise RuntimeError("Live smoke Codex workspace must stay disposable")
    return workspace_name


def _resolve_admin_database_url(
    database_url: str,
    explicit_admin_url: str | None,
) -> str:
    """Resolve only a loopback PostgreSQL admin target without logging conninfo."""

    candidate = explicit_admin_url
    if candidate is None:
        if len(database_url) > MAX_DATABASE_CONNINFO_LENGTH:
            raise RuntimeError("Live smoke requires a valid local admin database URL")
        try:
            candidate = make_conninfo(database_url, dbname=_ADMIN_DATABASE_NAME)
        except psycopg.ProgrammingError:
            raise RuntimeError("Live smoke requires a valid local admin database URL") from None
    if len(candidate) > MAX_DATABASE_CONNINFO_LENGTH:
        raise RuntimeError("Live smoke requires a valid local admin database URL")
    try:
        parameters = conninfo_to_dict(candidate)
    except psycopg.ProgrammingError:
        raise RuntimeError("Live smoke requires a valid local admin database URL") from None
    if parameters.get("dbname") != _ADMIN_DATABASE_NAME:
        raise RuntimeError("Live smoke admin database must be postgres")
    _validate_loopback_connection(parameters)
    return candidate


def _validate_loopback_connection(parameters: Mapping[str, str]) -> None:
    if "service" in parameters:
        raise RuntimeError("Live smoke admin database must use a loopback host")
    hosts = parameters.get("host", "").split(",")
    host_addresses = parameters.get("hostaddr", "").split(",")
    if not hosts or any(host not in _LOOPBACK_HOSTS for host in hosts):
        raise RuntimeError("Live smoke admin database must use a loopback host")
    if parameters.get("hostaddr") and any(
        address not in {"127.0.0.1", "::1"} for address in host_addresses
    ):
        raise RuntimeError("Live smoke admin database must use a loopback host")


async def _read_codex_version(executable: str, *, cwd: Path) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=codex_child_environment(),
        )
    except OSError:
        raise RuntimeError("Codex version command could not be started") from None
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=_CODEX_VERSION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise RuntimeError("Codex version command timed out") from None
    if process.returncode != 0:
        raise RuntimeError("Codex version command failed")
    return parse_codex_version(stdout)


async def _wait_until_started(
    server: uvicorn.Server,
    server_task: asyncio.Task[None] | None = None,
) -> None:
    for _ in range(1_000):
        if server.started:
            return
        if server_task is not None and server_task.done():
            await server_task
            break
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
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            raise RuntimeError("Streaming endpoint did not return text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ")
            elif line.startswith("data: ") and current_event is not None:
                payload = json.loads(line.removeprefix("data: "))
                if not isinstance(payload, dict):
                    raise RuntimeError("Live SSE returned a non-object event payload")
                events.append((current_event, payload))
                if current_event == "answer.error":
                    raise RuntimeError("Live SSE returned answer.error")
                current_event = None
    return events


if __name__ == "__main__":
    asyncio.run(main())
