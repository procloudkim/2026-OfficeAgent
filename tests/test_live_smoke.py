"""Offline contract tests for the optional real-runtime smoke harness."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager, nullcontext
from pathlib import Path
from typing import Any

import pytest
from psycopg.conninfo import conninfo_to_dict

import officeagent.infrastructure.test_runner as test_runner
import scripts.live_smoke as live_smoke
import scripts.live_smoke_contract as live_smoke_contract
from officeagent.infrastructure.settings import Settings
from officeagent.infrastructure.test_runner import TEST_DATABASE_NAME, replace_database_name
from scripts.live_smoke import (
    PROJECT_ROOT,
    _disposable_live_settings,
    _isolated_data_directory,
    _resolve_admin_database_url,
    _sample_document_path,
)
from scripts.live_smoke_contract import (
    build_live_receipt,
    parse_codex_version,
)

READY = {
    "status": "ready",
    "migration_head": "0003",
    "vector_version": "0.8.6",
}
CREATED_UPLOAD = {
    "document_id": "document-1",
    "document_version": 1,
    "change": "created",
    "invalidated_cache_entries": 0,
}
UPDATED_UPLOAD = {
    "document_id": "document-1",
    "document_version": 2,
    "change": "updated",
    "invalidated_cache_entries": 1,
}


def _events(
    *,
    cache_hit: bool,
    document_version: int,
    delta_count: int = 2,
) -> list[tuple[str, dict[str, Any]]]:
    request_id = f"request-{document_version}-{cache_hit}"
    cache_match = "exact" if cache_hit else None
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "answer.started",
            {
                "request_id": request_id,
                "cache_hit": cache_hit,
                "cache_match": cache_match,
            },
        )
    ]
    events.extend(
        ("answer.delta", {"request_id": request_id, "delta": f"delta-{index}"})
        for index in range(delta_count)
    )
    events.extend(
        [
            (
                "answer.citation",
                {
                    "request_id": request_id,
                    "document_id": "document-1",
                    "document_version": document_version,
                    "filename": "company-policy.txt",
                    "chunk_id": f"chunk-{document_version}",
                    "chunk_index": 0,
                    "page_number": None,
                    "start_offset": 0,
                    "end_offset": 42,
                    "quote": "직무 관련 교육비 지원 근거",
                    "score": 0.91,
                },
            ),
            (
                "answer.completed",
                {
                    "request_id": request_id,
                    "cache_hit": cache_hit,
                    "cache_match": cache_match,
                    "grounded": True,
                    "cache_written": not cache_hit,
                    "cache_skip_reason": "cache_hit" if cache_hit else None,
                },
            ),
        ]
    )
    return events


def _receipt_arguments() -> dict[str, Any]:
    return {
        "readiness": dict(READY),
        "created_upload": dict(CREATED_UPLOAD),
        "updated_upload": dict(UPDATED_UPLOAD),
        "initial_events": _events(cache_hit=False, document_version=1),
        "cached_events": _events(cache_hit=True, document_version=1, delta_count=1),
        "updated_events": _events(cache_hit=False, document_version=2),
        "document_name": "company-policy.txt",
        "codex_version": "codex-cli 0.146.0",
    }


def test_live_receipt_proves_g7_citation_miss_hit_and_update_miss() -> None:
    receipt = build_live_receipt(**_receipt_arguments())

    assert receipt["status"] == "ok"
    assert receipt["transport"] == "uvicorn-http-sse"
    assert receipt["database_isolation"] == "dedicated-disposable"
    assert receipt["codex_version"] == "codex-cli 0.146.0"
    assert receipt["readiness"] == READY
    assert receipt["document_id"] == "document-1"
    assert receipt["document_versions"] == {"created": 1, "updated": 2}
    assert receipt["cache_sequence"] == ["miss", "exact_hit", "miss_after_update"]
    assert receipt["phases"]["initial_miss"]["delta_count"] == 2
    assert receipt["phases"]["exact_hit"]["delta_count"] == 1
    assert receipt["phases"]["update_miss"]["delta_count"] == 2
    assert receipt["phases"]["initial_miss"]["citations"][0] == {
        "document_id": "document-1",
        "document_version": 1,
        "filename": "company-policy.txt",
        "chunk_id": "chunk-1",
        "chunk_index": 0,
        "page_number": None,
        "start_offset": 0,
        "end_offset": 42,
    }
    assert receipt["phases"]["update_miss"]["citations"][0]["document_version"] == 2

    serialized = json.dumps(receipt)
    for forbidden in ("answer", "data_dir", "database_url", "postgresql://", "secret"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (
            lambda arguments: arguments["cached_events"][0][1].update(
                cache_hit=False,
                cache_match=None,
            ),
            "exact cache hit",
        ),
        (
            lambda arguments: arguments["updated_events"][0][1].update(
                cache_hit=True,
                cache_match="exact",
            ),
            "miss after update",
        ),
        (
            lambda arguments: arguments["initial_events"][0][1].pop("cache_match"),
            "initial cache miss",
        ),
        (
            lambda arguments: arguments["updated_events"][-1][1].pop("cache_skip_reason"),
            "miss after update",
        ),
        (
            lambda arguments: arguments["updated_events"][-2][1].update(document_version=1),
            "citation",
        ),
        (
            lambda arguments: arguments["initial_events"][-2][1].pop("page_number"),
            "citation",
        ),
        (
            lambda arguments: arguments["updated_upload"].update(invalidated_cache_entries=0),
            "invalidate exactly one cache entry",
        ),
    ],
    ids=(
        "missing-hit",
        "missing-update-miss",
        "missing-null-start-field",
        "missing-null-completed-field",
        "stale-citation",
        "missing-null-citation-field",
        "no-invalidation",
    ),
)
def test_live_receipt_rejects_broken_g7_transition(
    corrupt: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    arguments = _receipt_arguments()
    corrupt(arguments)

    with pytest.raises(RuntimeError, match=message):
        build_live_receipt(**arguments)


def test_live_receipt_requires_multiple_real_deltas() -> None:
    arguments = _receipt_arguments()
    arguments["initial_events"] = _events(
        cache_hit=False,
        document_version=1,
        delta_count=1,
    )

    with pytest.raises(RuntimeError, match="at least two"):
        build_live_receipt(**arguments)


def test_readiness_receipt_is_exact_and_rejects_unexpected_runtime_state() -> None:
    receipt = live_smoke_contract.build_readiness_receipt(
        http_status=200,
        readiness=READY,
    )

    assert json.dumps(receipt, separators=(",", ":")) == (
        '{"command_status":"PASS","http_status":200,"migration_head":"0003",'
        '"status":"ready","vector_version":"0.8.6"}'
    )
    with pytest.raises(RuntimeError, match="readiness"):
        live_smoke_contract.build_readiness_receipt(
            http_status=200,
            readiness={**READY, "vector_version": "unexpected"},
        )


@pytest.mark.asyncio
async def test_readiness_only_main_skips_codex_documents_and_llm_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(data_dir=tmp_path, _env_file=None)
    events: list[str] = []

    @contextmanager
    def fake_data_directory() -> Any:
        events.append("workspace-enter")
        try:
            yield tmp_path
        finally:
            events.append("workspace-exit")

    @contextmanager
    def fake_disposable_settings(
        base_settings: Settings,
        data_dir: Path,
    ) -> Any:
        assert base_settings.data_dir == settings.data_dir
        assert data_dir == tmp_path
        events.append("database-enter")
        try:
            yield settings
        finally:
            events.append("database-exit")

    async def fake_readiness_smoke(active_settings: Settings) -> dict[str, Any]:
        assert active_settings is settings
        events.append("readiness")
        return {
            "command_status": "PASS",
            "http_status": 200,
            "migration_head": "0003",
            "status": "ready",
            "vector_version": "0.8.6",
        }

    async def forbidden_async(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("readiness-only invoked a Codex or LLM path")

    def forbidden_sync(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("readiness-only accessed the sample document")

    monkeypatch.setattr(live_smoke, "_runner_settings", lambda: settings, raising=False)
    monkeypatch.setattr(live_smoke, "_isolated_data_directory", fake_data_directory)
    monkeypatch.setattr(live_smoke, "_disposable_live_settings", fake_disposable_settings)
    monkeypatch.setattr(
        live_smoke,
        "_run_readiness_smoke",
        fake_readiness_smoke,
        raising=False,
    )
    monkeypatch.setattr(live_smoke, "_read_codex_version", forbidden_async)
    monkeypatch.setattr(live_smoke, "_run_live_smoke", forbidden_async)
    monkeypatch.setattr(live_smoke, "_sample_document_path", forbidden_sync)

    await live_smoke.main(["--readiness-only"])

    assert capsys.readouterr().out == (
        '{"command_status":"PASS","http_status":200,"migration_head":"0003",'
        '"status":"ready","vector_version":"0.8.6"}\n'
    )
    assert events == [
        "workspace-enter",
        "database-enter",
        "readiness",
        "database-exit",
        "workspace-exit",
    ]


@pytest.mark.asyncio
async def test_default_main_preserves_live_mode_and_json_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(data_dir=tmp_path, _env_file=None)
    calls: list[str] = []

    async def fake_codex_version(executable: str, *, cwd: Path) -> str:
        assert executable == settings.codex_executable
        assert cwd == tmp_path
        calls.append("version")
        return "codex-cli 0.146.0"

    async def fake_live_smoke(
        active_settings: Settings,
        *,
        codex_version: str,
    ) -> dict[str, Any]:
        assert active_settings is settings
        assert codex_version == "codex-cli 0.146.0"
        calls.append("live")
        return {"status": "ok"}

    monkeypatch.setattr(live_smoke, "_runner_settings", lambda: settings, raising=False)
    monkeypatch.setattr(
        live_smoke,
        "_isolated_data_directory",
        lambda: nullcontext(tmp_path),
    )
    monkeypatch.setattr(
        live_smoke,
        "_disposable_live_settings",
        lambda base_settings, data_dir: nullcontext(settings),
    )
    monkeypatch.setattr(live_smoke, "_read_codex_version", fake_codex_version)
    monkeypatch.setattr(live_smoke, "_run_live_smoke", fake_live_smoke)

    await live_smoke.main([])

    assert calls == ["version", "live"]
    assert capsys.readouterr().out == '{"status": "ok"}\n'


@pytest.mark.asyncio
async def test_readiness_smoke_calls_only_health_endpoint_and_stops_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def fake_running_server(settings: Settings) -> Any:
        events.append("server-enter")
        try:
            yield 32123
        finally:
            events.append("server-exit")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            events.append("raise-for-status")

        def json(self) -> dict[str, Any]:
            return dict(READY)

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            events.append("client-enter")
            return self

        async def __aexit__(self, *args: Any) -> None:
            events.append("client-exit")

        async def get(self, path: str) -> FakeResponse:
            events.append(f"get:{path}")
            return FakeResponse()

        async def post(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("readiness smoke called a document or question endpoint")

        def stream(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("readiness smoke called the LLM SSE endpoint")

    monkeypatch.setattr(
        live_smoke,
        "_running_server",
        fake_running_server,
        raising=False,
    )
    def fake_http_client(
        settings: Settings,
        port: int,
        *,
        timeout_seconds: float | None = None,
    ) -> FakeClient:
        assert port == 32123
        events.append(f"timeout:{timeout_seconds}")
        return FakeClient()

    monkeypatch.setattr(live_smoke, "_http_client", fake_http_client)
    monkeypatch.setattr(
        live_smoke,
        "_read_codex_version",
        lambda *args, **kwargs: pytest.fail("Codex version must not run"),
    )
    monkeypatch.setattr(
        live_smoke,
        "_sample_document_path",
        lambda: pytest.fail("sample document must not be read"),
    )

    receipt = await live_smoke._run_readiness_smoke(Settings(_env_file=None))

    assert receipt == {
        "command_status": "PASS",
        "http_status": 200,
        "migration_head": "0003",
        "status": "ready",
        "vector_version": "0.8.6",
    }
    assert events == [
        "server-enter",
        "timeout:10.0",
        "client-enter",
        "get:/health/ready",
        "raise-for-status",
        "client-exit",
        "server-exit",
    ]


def test_disposable_live_database_never_targets_product_and_drops_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    product_url = "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent"
    base_settings = Settings(
        database_url=product_url,
        database_admin_url=None,
        codex_workspace_directory_name="/mnt/d/private-repository",
        _env_file=None,
    )
    lifecycle: list[tuple[str, str, str]] = []

    def fake_create(admin_url: str, database_name: str) -> str:
        lifecycle.append(("create", conninfo_to_dict(admin_url)["dbname"], database_name))
        return replace_database_name(admin_url, database_name)

    def fake_drop(admin_url: str, database_name: str) -> None:
        lifecycle.append(("drop", conninfo_to_dict(admin_url)["dbname"], database_name))

    monkeypatch.setattr(test_runner, "create_database", fake_create)
    monkeypatch.setattr(test_runner, "drop_database", fake_drop)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with _disposable_live_settings(base_settings, tmp_path) as active_settings:
            database_name = conninfo_to_dict(active_settings.database_url)["dbname"]
            assert TEST_DATABASE_NAME.fullmatch(database_name)
            assert database_name != "officeagent"
            assert active_settings.data_dir == tmp_path
            assert active_settings.codex_workspace_path == tmp_path / "codex-workspace"
            raise RuntimeError("synthetic failure")

    assert lifecycle[0][0:2] == ("create", "postgres")
    assert lifecycle[1][0:2] == ("drop", "postgres")
    assert lifecycle[0][2] == lifecycle[1][2]
    assert TEST_DATABASE_NAME.fullmatch(lifecycle[0][2])
    assert conninfo_to_dict(base_settings.database_url)["dbname"] == "officeagent"


def test_disposable_live_workspace_fails_closed_if_fixed_name_escapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        live_smoke,
        "_LIVE_CODEX_WORKSPACE_DIRECTORY_NAME",
        "../private-repository",
        raising=False,
    )
    monkeypatch.setattr(
        live_smoke,
        "temporary_database",
        lambda admin_url: nullcontext(
            replace_database_name(admin_url, "officeagent_test_0123456789ab")
        ),
    )

    with pytest.raises(RuntimeError, match="workspace"):
        with _disposable_live_settings(Settings(_env_file=None), tmp_path):
            pytest.fail("unsafe workspace must not be yielded")


def test_runner_settings_allowlists_database_targets_and_ignores_codex_spoofing() -> None:
    product_url = "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent"
    settings = live_smoke._runner_settings(
        {
            "OFFICEAGENT_DATABASE_URL": product_url,
            "OFFICEAGENT_CODEX_EXECUTABLE": "/tmp/fake-codex",
            "OFFICEAGENT_CODEX_WORKSPACE_DIRECTORY_NAME": "/mnt/d/private-repository",
        }
    )

    assert settings.database_url == product_url
    assert settings.codex_executable == "codex"
    assert settings.codex_workspace_directory_name == "codex-workspace"


def test_admin_url_is_derived_safely_or_rejects_product_target() -> None:
    derived = _resolve_admin_database_url(
        "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent?dbname=production",
        None,
    )
    assert conninfo_to_dict(derived)["dbname"] == "postgres"

    for unsafe_admin in (
        "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent",
        "postgresql://officeagent:not-logged@127.0.0.1:5432/postgres?dbname=production",
        "not-a-connection-string",
    ):
        with pytest.raises(RuntimeError, match="admin database") as exc_info:
            _resolve_admin_database_url(
                "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent",
                unsafe_admin,
            )
        assert "not-logged" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("database_url", "admin_url"),
    [
        (
            "postgresql://officeagent:not-logged@remote.example:5432/officeagent",
            None,
        ),
        (
            "postgresql://officeagent:not-logged@127.0.0.1:5432/officeagent",
            "postgresql://admin:not-logged@remote.example:5432/postgres",
        ),
    ],
    ids=("derived-remote", "explicit-remote"),
)
def test_admin_url_rejects_remote_hosts_without_leaking_credentials(
    database_url: str,
    admin_url: str | None,
) -> None:
    with pytest.raises(RuntimeError, match="loopback") as exc_info:
        _resolve_admin_database_url(database_url, admin_url)

    assert "not-logged" not in str(exc_info.value)


def test_http_client_is_loopback_only_and_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(live_smoke.httpx, "AsyncClient", fake_client)

    assert live_smoke._http_client(Settings(_env_file=None), port=32123) is sentinel
    assert captured["base_url"] == "http://127.0.0.1:32123"
    assert captured["timeout"] == 150.0
    assert captured["trust_env"] is False


def test_codex_version_is_normalized_without_arbitrary_output() -> None:
    assert parse_codex_version(b"codex-cli 0.146.0\n") == "codex-cli 0.146.0"
    for unsafe_output in (
        b"codex-cli 0.146.0\naccount=user@example.com\n",
        b"codex-cli unknown\n",
        b"secret\n",
    ):
        with pytest.raises(RuntimeError, match="version") as exc_info:
            parse_codex_version(unsafe_output)
        assert "user@example.com" not in str(exc_info.value)
        assert "secret" not in str(exc_info.value)


def test_live_data_directory_is_outside_project_and_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _sample_document_path() == PROJECT_ROOT / "sample-docs/company-policy.txt"

    with _isolated_data_directory() as data_dir:
        assert data_dir.is_dir()
        assert not data_dir.is_relative_to(PROJECT_ROOT)

    assert not data_dir.exists()
