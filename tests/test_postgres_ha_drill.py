"""Offline contracts for the disposable PostgreSQL streaming-standby drill."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import officeagent.infrastructure.postgres_ha_drill as ha_module
from officeagent.infrastructure.migrations import discover_migrations
from officeagent.infrastructure.postgres_ha_drill import (
    DEFAULT_COMPOSE_FILE,
    CommandResult,
    HaDrillError,
    HaDrillObservation,
    PostgresHaDrill,
    build_ha_environment,
    build_ha_receipt,
    validate_ha_compose_definition,
    validate_local_docker_endpoint,
)

EXPECTED_MIGRATIONS = tuple(
    f"{migration.version}:{migration.checksum}" for migration in discover_migrations()
)
EXPECTED_MIGRATION_OUTPUT = "\n".join(EXPECTED_MIGRATIONS)
EXPECTED_HEAD = discover_migrations()[-1].version

VALID_OBSERVATION = HaDrillObservation(
    postgres_major=17,
    migration_head=EXPECTED_HEAD,
    primary_migrations_verified=True,
    standby_migrations_verified=True,
    replication_state="streaming",
    synchronization_state="async",
    standby_was_in_recovery=True,
    marker_replayed=True,
    replay_lsn_reached=True,
    pre_fence_route_node="primary",
    old_primary_stopped=True,
    standby_readable_after_fence=True,
    standby_passfile_on_tmpfs=True,
    route_rejected_before_promotion=True,
    any_fallback_route_verified=True,
    promotion_succeeded=True,
    post_promotion_route_node="standby",
    post_promotion_write_count=1,
    old_primary_remained_stopped=True,
)


def test_receipt_is_explicitly_local_async_and_does_not_overclaim_ha() -> None:
    receipt = build_ha_receipt(VALID_OBSERVATION)

    assert receipt == {
        "schema_version": 1,
        "status": "PASS",
        "classification": "local_async_streaming_standby_drill",
        "scope": {
            "single_docker_backend": True,
            "production_ha": False,
            "automatic_failover": False,
            "zero_data_loss": False,
            "rpo_rto_validated": False,
        },
        "postgres_major": 17,
        "migration": {
            "head": EXPECTED_HEAD,
            "checksums_verified_on_both_nodes": True,
        },
        "replication": {
            "state": "streaming",
            "synchronization": "async",
            "marker_replayed": True,
            "replay_lsn_reached": True,
        },
        "fencing": {
            "old_primary_stopped": True,
            "standby_readable_after_fence": True,
            "read_write_route_rejected_before_promotion": True,
            "same_route_any_fallback_verified": True,
        },
        "credentials": {"replication_passfile_storage": "runtime_tmpfs"},
        "promotion": {
            "method": "manual_pg_promote",
            "same_route_selected": "standby",
            "post_promotion_write": True,
            "old_primary_remained_stopped": True,
        },
        "cleanup": "completed_before_receipt",
    }
    encoded = json.dumps(receipt)
    for forbidden in (
        "postgresql://",
        "password",
        "secret",
        "project_name",
        "container",
        "volume",
        "0/16b6",
    ):
        assert forbidden not in encoded.casefold()


@pytest.mark.parametrize(
    ("field", "unsafe_value", "message"),
    [
        ("postgres_major", 16, "PostgreSQL 17"),
        ("migration_head", "3", "migration head"),
        ("primary_migrations_verified", False, "migration"),
        ("standby_migrations_verified", False, "migration"),
        ("replication_state", "catchup", "streaming"),
        ("synchronization_state", "sync", "asynchronous"),
        ("standby_was_in_recovery", False, "recovery"),
        ("marker_replayed", False, "marker"),
        ("replay_lsn_reached", False, "replay LSN"),
        ("pre_fence_route_node", "standby", "pre-fence route"),
        ("old_primary_stopped", False, "fenced"),
        ("standby_readable_after_fence", False, "readable"),
        ("standby_passfile_on_tmpfs", False, "tmpfs"),
        ("route_rejected_before_promotion", False, "route"),
        ("any_fallback_route_verified", False, "any fallback"),
        ("promotion_succeeded", False, "promotion"),
        ("post_promotion_route_node", "primary", "promoted standby"),
        ("post_promotion_write_count", 0, "write"),
        ("old_primary_remained_stopped", False, "remain fenced"),
    ],
)
def test_receipt_fails_closed_when_any_ha_proof_is_missing(
    field: str,
    unsafe_value: object,
    message: str,
) -> None:
    observation = replace(VALID_OBSERVATION, **{field: unsafe_value})

    with pytest.raises(HaDrillError, match=message):
        build_ha_receipt(observation)


def test_docker_endpoint_must_be_a_local_unix_socket() -> None:
    assert validate_local_docker_endpoint('"unix:///var/run/docker.sock"') == (
        "unix:///var/run/docker.sock"
    )
    for unsafe in (
        '"tcp://10.0.0.5:2376"',
        '"ssh://operator@example.internal"',
        '"npipe:////./pipe/docker_engine"',
        "not-json",
    ):
        with pytest.raises(HaDrillError, match="local Unix socket"):
            validate_local_docker_endpoint(unsafe)


def test_environment_forwards_no_product_or_llm_credentials() -> None:
    environment = build_ha_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/tester",
            "DOCKER_CONTEXT": "desktop-linux",
            "OFFICEAGENT_DATABASE_URL": "postgresql://product:secret@db/product",
            "OPENAI_API_KEY": "must-not-leak",
            "CODEX_HOME": "/private/codex",
            "COMPOSE_FILE": "/tmp/override.yaml",
        },
        password="ephemeral-only",
    )

    assert environment == {
        "PATH": "/usr/bin",
        "HOME": "/home/tester",
        "DOCKER_CONTEXT": "desktop-linux",
        "OFFICEAGENT_HA_PASSWORD": "ephemeral-only",
    }


@pytest.mark.parametrize(
    "docker_host",
    ("unix:///var/run/docker.sock", "tcp://10.0.0.5:2376", "ssh://prod"),
)
def test_environment_rejects_docker_host_override(docker_host: str) -> None:
    with pytest.raises(HaDrillError, match="DOCKER_HOST override"):
        build_ha_environment(
            {"PATH": "/usr/bin", "DOCKER_HOST": docker_host},
            password="ephemeral-only",
        )


def test_compose_contract_is_manual_internal_and_credential_isolated() -> None:
    compose = _valid_compose_text()

    validate_ha_compose_definition(compose)

    for corrupt, message in (
        (compose + "\nports:\n  - '5432:5432'\n", "host ports"),
        (compose + "\nrestart: always\n", "restart policy"),
        (compose + "\nnetwork_mode: host\n", "host networking"),
        (compose + "\nvolumes:\n  - /var/run/docker.sock:/var/run/docker.sock\n", "socket"),
        (compose + "\nenvironment:\n  OPENAI_API_KEY: token\n", "LLM credentials"),
        (compose + "\nimage: patroni:latest\n", "automatic failover"),
        (
            compose.replace("${OFFICEAGENT_HA_PASSWORD:?required}", "checked-in"),
            "ephemeral password",
        ),
        (
            compose.replace(
                "PGTARGETSESSIONATTRS: read-write", "PGTARGETSESSIONATTRS: any"
            ),
            "routing",
        ),
        (
            compose.replace("PGCONNECT_TIMEOUT: \"2\"", "PGCONNECT_TIMEOUT: \"60\""),
            "timeout",
        ),
        (
            compose.replace(
                "command: [\"officeagent-migrate\"]", "command: [\"true\"]"
            ),
            "migration",
        ),
    ):
        with pytest.raises(HaDrillError, match=message):
            validate_ha_compose_definition(corrupt)


def test_repository_ha_compose_matches_the_offline_contract() -> None:
    validate_ha_compose_definition(DEFAULT_COMPOSE_FILE.read_text(encoding="utf-8"))


def test_repository_ha_compose_keeps_replication_passfile_out_of_pgdata() -> None:
    compose = DEFAULT_COMPOSE_FILE.read_text(encoding="utf-8")

    assert "passfile=/run/officeagent-ha/.pgpass" in compose
    assert '"$${PGDATA}/.pgpass"' not in compose
    assert "/run/officeagent-ha:rw,noexec,nosuid,nodev" in compose


def test_repository_primary_waits_for_the_target_database_not_only_the_socket() -> None:
    compose = DEFAULT_COMPOSE_FILE.read_text(encoding="utf-8")

    assert "--command 'SELECT 1'" in compose
    assert "until pg_isready" not in compose


def test_ci_only_validates_the_ha_model_and_does_not_run_failover() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/quality-gates.yml"
    ).read_text(encoding="utf-8")

    assert (
        "docker compose --file compose.ha-local.yaml --profile tools config --quiet"
        in workflow
    )
    assert "python -m officeagent.infrastructure.postgres_ha_drill" not in workflow


def test_cli_sanitizes_unexpected_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_without_exposing_message() -> dict[str, object]:
        raise RuntimeError("postgresql://credential@remote/product")

    monkeypatch.setattr(ha_module, "run_ha_drill", fail_without_exposing_message)

    assert ha_module.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "HA drill failed: unexpected internal failure\n"


def test_cli_reports_interrupt_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupt() -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(ha_module, "run_ha_drill", interrupt)

    assert ha_module.main([]) == 130
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "HA drill interrupted\n"


def test_successful_plan_proves_route_fence_and_manual_promotion_in_order(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor()
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    receipt = drill.run()

    assert receipt["status"] == "PASS"
    ordered = (
        "apply-schema",
        "primary-migrations",
        "init-standby",
        "standby-credential-storage",
        "standby-recovery-state",
        "replication-status",
        "standby-replay",
        "pre-fence-route",
        "stop-primary",
        "primary-fenced",
        "standby-readable-after-fence",
        "pre-promotion-route",
        "pre-promotion-read-only-route",
        "promote-standby",
        "standby-promoted",
        "post-promotion-route",
        "standby-migrations",
        "primary-remains-fenced",
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    )
    assert [executor.steps.index(step) for step in ordered] == sorted(
        executor.steps.index(step) for step in ordered
    )
    assert executor.steps[-4:] == [
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_all_route_probes_reuse_the_same_no_deps_multi_host_service(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor()
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    drill.run()

    route_arguments = [
        executor.arguments[executor.steps.index(step)]
        for step in ("pre-fence-route", "pre-promotion-route", "post-promotion-route")
    ]
    assert all(
        arguments[-6:-1]
        == ("run", "--rm", "--no-deps", "route-probe", "--command")
        for arguments in route_arguments
    )
    assert len({arguments[:-1] for arguments in route_arguments}) == 1
    fallback_arguments = executor.arguments[
        executor.steps.index("pre-promotion-read-only-route")
    ]
    assert fallback_arguments[-8:-1] == (
        "run",
        "--rm",
        "--no-deps",
        "--env",
        "PGTARGETSESSIONATTRS=any",
        "route-probe",
        "--command",
    )
    assert all(
        "ephemeral-only" not in argument
        and "postgresql://" not in argument
        and "OFFICEAGENT_HA_PASSWORD=" not in argument
        for arguments in executor.arguments
        for argument in arguments
    )
    stop_index = executor.steps.index("stop-primary")
    for step, arguments in zip(
        executor.steps[stop_index + 1 :],
        executor.arguments[stop_index + 1 :],
        strict=True,
    ):
        if "run" in arguments:
            assert "--no-deps" in arguments, step
        assert not ("up" in arguments and "primary" in arguments), step


def test_cleanup_stops_both_nodes_before_removing_volumes_and_local_image(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(fail_step="replication-status")
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="replication-status failed"):
        drill.run()

    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]
    assert executor.arguments[-6][-5:] == (
        "stop",
        "--timeout",
        "30",
        "primary",
        "standby",
    )
    assert executor.arguments[-5][-7:] == (
        "--profile",
        "tools",
        "down",
        "--volumes",
        "--remove-orphans",
        "--rmi",
        "local",
    )


def test_cleanup_retries_teardown_only_after_nodes_are_fenced(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(cleanup_failures=1)
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    assert drill.run()["status"] == "PASS"
    assert executor.steps.count("cleanup-fence") == 1
    assert executor.steps.count("cleanup") == 2
    assert executor.steps.count("cleanup-containers") == 2
    assert executor.steps.count("cleanup-volumes") == 2
    assert executor.steps.count("cleanup-image") == 2
    assert executor.steps.count("cleanup-network") == 2


def test_cleanup_failure_never_issues_a_pass_receipt(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(cleanup_failures=3)
    project_name = "officeagent-ha-a1b2c3d4e5f6"
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name=project_name,
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match=project_name):
        drill.run()
    assert executor.steps.count("cleanup-fence") == 1
    assert executor.steps.count("cleanup") == 3
    assert executor.steps.count("cleanup-containers") == 3
    assert executor.steps.count("cleanup-volumes") == 3
    assert executor.steps.count("cleanup-image") == 3
    assert executor.steps.count("cleanup-network") == 3


def test_cleanup_residue_never_issues_a_pass_receipt(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(output_overrides={"cleanup-volumes": "residual-volume\n"})
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="cleanup"):
        drill.run()
    assert executor.steps.count("cleanup") == 3
    assert executor.steps.count("cleanup-containers") == 3
    assert executor.steps.count("cleanup-volumes") == 3
    assert executor.steps.count("cleanup-image") == 3
    assert executor.steps.count("cleanup-network") == 3


def test_cleanup_fence_executor_error_still_runs_verified_teardown(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = CleanupFenceErrorExecutor()
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    assert drill.run()["status"] == "PASS"
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_pre_promotion_route_must_fail_or_drill_fails_closed(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(pre_promotion_route_returncode=0)
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="accepted a read-write target"):
        drill.run()
    assert "promote-standby" not in executor.steps
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_nonzero_read_write_probe_requires_same_route_recovery_proof(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(
        output_overrides={"pre-promotion-read-only-route": "primary|f\n"}
    )
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="fallback route"):
        drill.run()
    assert "promote-standby" not in executor.steps


def test_wrong_pre_fence_route_stops_before_fencing_or_promotion(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(output_overrides={"pre-fence-route": "standby\n"})
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="pre-fence route"):
        drill.run()
    assert "stop-primary" not in executor.steps
    assert "promote-standby" not in executor.steps
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_unreadable_standby_stops_before_route_probe_or_promotion(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(
        output_overrides={"standby-readable-after-fence": "t|f\n"}
    )
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="not readable"):
        drill.run()
    assert "pre-promotion-route" not in executor.steps
    assert "promote-standby" not in executor.steps
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_rejected_manual_promotion_stops_before_post_promotion_route(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(output_overrides={"promote-standby": "f\n"})
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="promotion was not accepted"):
        drill.run()
    assert "post-promotion-route" not in executor.steps
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_migration_checksum_mismatch_fails_closed_and_cleans_up(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(
        output_overrides={"standby-migrations": "0001:" + "0" * 64 + "\n"}
    )
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(HaDrillError, match="standby migration ledger"):
        drill.run()
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


def test_interrupt_still_fences_and_cleans_the_disposable_topology(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.ha-local.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = InterruptingExecutor()
    drill = PostgresHaDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-ha-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(KeyboardInterrupt):
        drill.run()
    assert executor.steps[-6:] == [
        "cleanup-fence",
        "cleanup",
        "cleanup-containers",
        "cleanup-volumes",
        "cleanup-image",
        "cleanup-network",
    ]


class FakeExecutor:
    def __init__(
        self,
        *,
        fail_step: str | None = None,
        cleanup_failures: int = 0,
        pre_promotion_route_returncode: int = 1,
        output_overrides: dict[str, str] | None = None,
    ) -> None:
        self.fail_step = fail_step
        self.cleanup_failures = cleanup_failures
        self.pre_promotion_route_returncode = pre_promotion_route_returncode
        self.output_overrides = output_overrides or {}
        self.steps: list[str] = []
        self.arguments: list[tuple[str, ...]] = []

    def execute(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        timeout_seconds: float,
        step: str,
    ) -> CommandResult:
        del timeout_seconds
        self.steps.append(step)
        self.arguments.append(arguments)
        assert environment["OFFICEAGENT_HA_PASSWORD"] == "ephemeral-only"
        if step == "cleanup" and self.cleanup_failures:
            self.cleanup_failures -= 1
            return CommandResult(returncode=1, stdout="transient teardown race")
        if step == self.fail_step:
            return CommandResult(returncode=1, stdout="must not be exposed")
        if step == "pre-promotion-route":
            return CommandResult(
                returncode=self.pre_promotion_route_returncode,
                stdout="" if self.pre_promotion_route_returncode else "standby\n",
            )
        outputs = {
            "docker-endpoint": '"unix:///var/run/docker.sock"\n',
            "primary-version": "170010\n",
            "primary-migrations": EXPECTED_MIGRATION_OUTPUT + "\n",
            "standby-recovery-state": "t\n",
            "replication-status": "streaming|async\n",
            "primary-marker-lsn": "0/16B6A80\n",
            "standby-replay": "t\n",
            "pre-fence-route": "primary\n",
            "primary-fenced": "",
            "standby-readable-after-fence": "t|t\n",
            "standby-credential-storage": "",
            "pre-promotion-read-only-route": "standby|t\n",
            "promote-standby": "t\n",
            "standby-promoted": "f\n",
            "post-promotion-route": "standby|1\n",
            "post-promotion-marker": "1\n",
            "standby-migrations": EXPECTED_MIGRATION_OUTPUT + "\n",
            "primary-remains-fenced": "",
            "cleanup-containers": "",
            "cleanup-volumes": "",
            "cleanup-image": "",
            "cleanup-network": "",
        }
        outputs.update(self.output_overrides)
        return CommandResult(returncode=0, stdout=outputs.get(step, ""))


class InterruptingExecutor(FakeExecutor):
    def execute(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        timeout_seconds: float,
        step: str,
    ) -> CommandResult:
        if step == "replication-status":
            self.steps.append(step)
            self.arguments.append(arguments)
            raise KeyboardInterrupt
        return super().execute(
            arguments,
            environment=environment,
            timeout_seconds=timeout_seconds,
            step=step,
        )


class CleanupFenceErrorExecutor(FakeExecutor):
    def execute(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        timeout_seconds: float,
        step: str,
    ) -> CommandResult:
        if step == "cleanup-fence":
            self.steps.append(step)
            self.arguments.append(arguments)
            raise HaDrillError("cleanup-fence could not run")
        return super().execute(
            arguments,
            environment=environment,
            timeout_seconds=timeout_seconds,
            step=step,
        )


def _valid_compose_text() -> str:
    return """
x-postgres-image: &postgres-image
  pgvector/pgvector:0.8.6-pg17-bookworm@sha256:fixture
services:
  primary:
    image: *postgres-image
    environment:
      POSTGRES_PASSWORD: ${OFFICEAGENT_HA_PASSWORD:?required}
    command:
      - postgres
      - -c
      - wal_level=replica
      - -c
      - hot_standby=on
      - -c
      - cluster_name=primary
      - --command 'SELECT 1'
  standby-init:
    image: *postgres-image
    command: [pg_basebackup, --write-recovery-conf, standby.signal]
  standby:
    image: *postgres-image
    command:
      - postgres
      - -c
      - hot_standby=on
      - -c
      - cluster_name=standby
      - passfile=/run/officeagent-ha/.pgpass
    tmpfs:
      - /run/officeagent-ha:rw,noexec,nosuid,nodev
  migration:
    build:
      dockerfile: Dockerfile.test
    environment:
      OFFICEAGENT_DATABASE_URL: postgresql://postgres:${OFFICEAGENT_HA_PASSWORD:?required}@primary:5432/officeagent_ha
    command: ["officeagent-migrate"]
  route-probe:
    image: *postgres-image
    environment:
      PGPASSWORD: ${OFFICEAGENT_HA_PASSWORD:?required}
      PGHOST: primary,standby
      PGPORT: 5432,5432
      PGTARGETSESSIONATTRS: read-write
      PGCONNECT_TIMEOUT: "2"
    command: [psql]
networks:
  ha:
    internal: true
volumes:
  primary_data:
  standby_data:
"""
