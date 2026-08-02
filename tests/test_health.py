from typing import Any, cast

import anyio
import pytest
from httpx import ASGITransport, AsyncClient

import officeagent.bootstrap as bootstrap_module
import officeagent.main as main_module
from officeagent.api.app import ServiceContainer, create_app
from officeagent.application.ingestion import DocumentIngestion
from officeagent.application.question_answering import QuestionAnsweringService
from officeagent.domain.models import ReadinessStatus
from officeagent.domain.ports import ReadinessProbe
from officeagent.infrastructure.migrations import PostgresReadinessProbe
from officeagent.infrastructure.settings import Settings
from officeagent.main import app


class FailingReadinessProbe:
    async def check(self) -> ReadinessStatus:
        raise RuntimeError("postgresql://user:secret@database/officeagent")


class SuccessfulReadinessProbe:
    async def check(self) -> ReadinessStatus:
        return ReadinessStatus(migration_head="0001", vector_version="0.8.6")


def _services_with(
    readiness: ReadinessProbe,
    *,
    readiness_instance_nonce: str | None = None,
) -> ServiceContainer:
    return ServiceContainer(
        ingestion=cast(DocumentIngestion, object()),
        questions=cast(QuestionAnsweringService, object()),
        readiness=readiness,
        readiness_instance_nonce=readiness_instance_nonce,
    )


async def test_liveness() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_response_and_failure_are_credential_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    readiness_app = create_app(_services_with(SuccessfulReadinessProbe()))

    async with readiness_app.router.lifespan_context(readiness_app):
        async with AsyncClient(
            transport=ASGITransport(app=readiness_app),
            base_url="http://test",
        ) as client:
            live_response = await client.get("/health/live")
            ready_response = await client.get("/health/ready")

    assert live_response.status_code == 200
    assert ready_response.status_code == 200
    assert ready_response.json() == {
        "status": "ready",
        "migration_head": "0001",
        "vector_version": "0.8.6",
    }

    failing_app = create_app(_services_with(FailingReadinessProbe()))
    async with failing_app.router.lifespan_context(failing_app):
        async with AsyncClient(
            transport=ASGITransport(app=failing_app),
            base_url="http://test",
        ) as client:
            failed_response = await client.get("/health/ready")

    assert failed_response.status_code == 503
    assert failed_response.json() == {"detail": "Service is not ready"}
    assert "secret" not in failed_response.text
    assert "secret" not in caplog.text
    assert "postgresql://" not in caplog.text


async def test_readiness_echoes_configured_instance_nonce() -> None:
    nonce = "a1" * 32
    readiness_app = create_app(
        _services_with(
            SuccessfulReadinessProbe(),
            readiness_instance_nonce=nonce,
        )
    )

    async with readiness_app.router.lifespan_context(readiness_app):
        async with AsyncClient(
            transport=ASGITransport(app=readiness_app),
            base_url="http://test",
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "migration_head": "0001",
        "vector_version": "0.8.6",
        "instance_nonce": nonce,
    }


@pytest.mark.parametrize(
    "value",
    ["", "0" * 63, "0" * 65, "A" * 64, "g" * 64],
)
def test_readiness_instance_nonce_rejects_invalid_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OFFICEAGENT_READINESS_INSTANCE_NONCE", value)

    with pytest.raises(ValueError) as error:
        Settings(_env_file=None)

    error_text = str(error.value)
    assert "input_value" not in error_text
    if value:
        assert value not in error_text


async def test_build_services_wires_readiness_instance_nonce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    nonce = "ab" * 32
    migrated_urls: list[str] = []
    monkeypatch.setattr(
        bootstrap_module,
        "apply_migrations",
        migrated_urls.append,
    )

    services = await bootstrap_module.build_services(
        Settings(
            data_dir=tmp_path,
            database_url="postgresql://officeagent:test@127.0.0.1:15432/test",
            readiness_instance_nonce=nonce,
            _env_file=None,
        )
    )

    assert migrated_urls == ["postgresql://officeagent:test@127.0.0.1:15432/test"]
    assert services.readiness_instance_nonce == nonce


async def test_postgres_readiness_has_endpoint_and_database_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    for invalid_timeout in (0.0, -1.0):
        with pytest.raises(ValueError, match="positive"):
            PostgresReadinessProbe(
                "postgresql://user:secret@database/officeagent",
                timeout_seconds=invalid_timeout,
            )
    for non_finite_timeout in (None, "5", True, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite number"):
            PostgresReadinessProbe(
                "postgresql://user:secret@database/officeagent",
                timeout_seconds=cast(Any, non_finite_timeout),
            )
    with pytest.raises(ValueError, match="at most"):
        PostgresReadinessProbe(
            "postgresql://user:secret@database/officeagent",
            timeout_seconds=30.01,
        )

    async def stalled_run_sync(
        inspection: Any,
        *,
        abandon_on_cancel: bool,
    ) -> None:
        captured["inspection"] = inspection
        captured["abandon_on_cancel"] = abandon_on_cancel
        await anyio.sleep_forever()

    monkeypatch.setattr(
        "officeagent.infrastructure.migrations.anyio.to_thread.run_sync",
        stalled_run_sync,
    )
    probe = PostgresReadinessProbe(
        "postgresql://user:secret@database/officeagent",
        timeout_seconds=0.05,
    )

    with pytest.raises(TimeoutError):
        await probe.check()

    assert captured["abandon_on_cancel"] is True
    assert captured["inspection"].keywords == {
        "connect_timeout_seconds": 1,
        "statement_timeout_ms": 50,
    }


def test_api_bind_defaults_are_safe_and_run_uses_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OFFICEAGENT_API_HOST", raising=False)
    monkeypatch.delenv("OFFICEAGENT_API_PORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000

    captured: dict[str, object] = {}

    def fake_run(application: str, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(api_host="127.0.0.2", api_port=43127, _env_file=None),
    )
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main_module.run()

    assert captured == {
        "application": "officeagent.main:app",
        "host": "127.0.0.2",
        "port": 43127,
    }


@pytest.mark.parametrize("value", ["1", "8000", "65535"])
def test_api_port_accepts_valid_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OFFICEAGENT_API_PORT", value)

    assert Settings(_env_file=None).api_port == int(value)


@pytest.mark.parametrize("value", ["0", "65536"])
def test_api_port_rejects_out_of_range_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OFFICEAGENT_API_PORT", value)

    with pytest.raises(ValueError):
        Settings(_env_file=None)
