"""Offline contract tests for the disposable PostgreSQL recovery drill."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from officeagent.infrastructure.postgres_recovery_drill import (
    DEFAULT_COMPOSE_FILE,
    CommandResult,
    DrillError,
    DrillObservation,
    PostgresRecoveryDrill,
    build_drill_environment,
    build_recovery_receipt,
    validate_compose_definition,
    validate_local_docker_endpoint,
)

VALID_OBSERVATION = DrillObservation(
    postgres_major=17,
    vector_version="0.8.6",
    logical_marker_count=1,
    logical_schema_present=True,
    base_backup_manifest_verified=True,
    wal_archive_count=2,
    recovered_markers=("base", "before_target"),
    recovery_promoted=True,
    recovery_signal_removed=True,
    recovered_schema_present=True,
)


def test_receipt_keeps_logical_restore_distinct_from_pitr_and_is_sanitized() -> None:
    receipt = build_recovery_receipt(VALID_OBSERVATION)

    assert receipt == {
        "schema_version": 1,
        "status": "PASS",
        "topology": "local_disposable_docker",
        "postgres_major": 17,
        "vector_version": "0.8.6",
        "logical_restore": {
            "classification": "logical_snapshot_restore",
            "method": "pg_dump_custom_pg_restore",
            "pitr": False,
            "marker_count": 1,
            "schema_present": True,
        },
        "pitr": {
            "classification": "physical_base_backup_wal_replay",
            "method": "pg_basebackup_recovery_target_time",
            "base_backup_manifest_verified": True,
            "wal_archive_count": 2,
            "before_target_present": True,
            "after_target_absent": True,
            "promoted": True,
            "recovery_signal_removed": True,
            "schema_present": True,
        },
        "cleanup": "completed_before_receipt",
    }
    encoded = json.dumps(receipt)
    for forbidden in (
        "postgresql://",
        "password",
        "secret",
        '"target_time":',
        "2026-08-04t01:02:03",
        "container",
        "volume",
        "project_name",
    ):
        assert forbidden not in encoded.casefold()


@pytest.mark.parametrize(
    ("field", "unsafe_value", "message"),
    [
        ("postgres_major", 16, "PostgreSQL 17"),
        ("logical_marker_count", 0, "logical restore"),
        ("logical_schema_present", False, "logical restore"),
        ("base_backup_manifest_verified", False, "base backup"),
        ("wal_archive_count", 0, "WAL archive"),
        ("recovered_markers", ("base", "before_target", "after_target"), "target"),
        ("recovery_promoted", False, "promote"),
        ("recovery_signal_removed", False, "recovery.signal"),
        ("recovered_schema_present", False, "recovered schema"),
    ],
)
def test_receipt_fails_closed_when_any_restore_proof_is_missing(
    field: str,
    unsafe_value: object,
    message: str,
) -> None:
    observation = replace(VALID_OBSERVATION, **{field: unsafe_value})

    with pytest.raises(DrillError, match=message):
        build_recovery_receipt(observation)


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
        with pytest.raises(DrillError, match="local Unix socket"):
            validate_local_docker_endpoint(unsafe)


def test_drill_environment_does_not_forward_product_or_llm_credentials() -> None:
    environment = build_drill_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/tester",
            "DOCKER_CONTEXT": "desktop-linux",
            "OFFICEAGENT_DATABASE_URL": "postgresql://product:secret@db/product",
            "OPENAI_API_KEY": "must-not-leak",
            "CODEX_HOME": "/private/codex",
        },
        password="ephemeral-only",
    )

    assert environment == {
        "PATH": "/usr/bin",
        "HOME": "/home/tester",
        "DOCKER_CONTEXT": "desktop-linux",
        "OFFICEAGENT_DRILL_PASSWORD": "ephemeral-only",
    }


@pytest.mark.parametrize(
    "docker_host",
    ("unix:///var/run/docker.sock", "tcp://10.0.0.5:2376", "ssh://prod"),
)
def test_drill_rejects_docker_host_override(docker_host: str) -> None:
    with pytest.raises(DrillError, match="DOCKER_HOST override"):
        build_drill_environment(
            {"PATH": "/usr/bin", "DOCKER_HOST": docker_host},
            password="ephemeral-only",
        )


def test_compose_contract_has_no_host_ports_or_persisted_credentials() -> None:
    compose = _valid_compose_text()

    validate_compose_definition(compose)

    for corrupt, message in (
        (compose + "\nports:\n  - '5432:5432'\n", "host ports"),
        (compose.replace("archive_mode=on", "archive_mode=off"), "archive_mode"),
        (compose.replace("recovery.signal", "standby.signal"), "recovery.signal"),
        (compose.replace("${OFFICEAGENT_DRILL_PASSWORD:?required}", "checked-in"), "password"),
        (compose.replace("pg_basebackup", "physical-copy"), "pg_basebackup"),
    ):
        with pytest.raises(DrillError, match=message):
            validate_compose_definition(corrupt)


def test_repository_recovery_compose_matches_the_offline_contract() -> None:
    validate_compose_definition(DEFAULT_COMPOSE_FILE.read_text(encoding="utf-8"))


def test_replication_hba_bootstrap_is_specific_and_contains_no_secret() -> None:
    init_script = (
        Path(__file__).parents[1] / "scripts/postgres_recovery_drill_init.sh"
    ).read_text(encoding="utf-8")

    assert (
        init_script.count(
            "host replication officeagent_drill_replication all scram-sha-256"
        )
        == 1
    )
    assert "CREATE ROLE officeagent_drill_replication LOGIN REPLICATION" in init_script
    assert " trust" not in init_script
    assert "PASSWORD '" not in init_script


def test_cleanup_runs_after_a_mid_drill_failure(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(fail_step="base-backup")
    drill = PostgresRecoveryDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-recovery-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(DrillError, match="base-backup failed"):
        drill.run()

    assert executor.steps[-1] == "cleanup"
    cleanup = executor.arguments[-1]
    assert cleanup[-5:] == (
        "--profile",
        "tools",
        "down",
        "--volumes",
        "--remove-orphans",
    )


def test_successful_command_plan_proves_actual_target_boundary_before_cleanup(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor()
    drill = PostgresRecoveryDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-recovery-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    receipt = drill.run()

    assert receipt["status"] == "PASS"
    assert executor.steps.index("logical-restore") < executor.steps.index("base-backup")
    assert executor.steps.index("base-verify") < executor.steps.index("insert-before")
    assert executor.steps.index("read-target-time") < executor.steps.index("insert-after")
    assert executor.steps.index("archive-ready") < executor.steps.index("prepare-recovery")
    assert executor.steps.index("recovery-markers") < executor.steps.index("cleanup")
    assert executor.steps[-1] == "cleanup"


def test_cleanup_retries_a_transient_compose_teardown_failure(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(cleanup_failures=1)
    drill = PostgresRecoveryDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-recovery-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    assert drill.run()["status"] == "PASS"
    assert executor.steps.count("cleanup") == 2


def test_terminal_cleanup_failure_names_only_the_disposable_project(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = FakeExecutor(cleanup_failures=3)
    project_name = "officeagent-recovery-a1b2c3d4e5f6"
    drill = PostgresRecoveryDrill(
        executor=executor,
        compose_file=compose_path,
        project_name=project_name,
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(DrillError, match=project_name):
        drill.run()
    assert executor.steps.count("cleanup") == 3


def test_probe_and_cleanup_failure_preserves_disposable_project_name(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    project_name = "officeagent-recovery-a1b2c3d4e5f6"
    drill = PostgresRecoveryDrill(
        executor=FakeExecutor(fail_step="base-backup", cleanup_failures=3),
        compose_file=compose_path,
        project_name=project_name,
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(DrillError, match=rf"base-backup failed.*{project_name}"):
        drill.run()


def test_cleanup_runs_when_the_drill_is_interrupted(tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.recovery-drill.yaml"
    compose_path.write_text(_valid_compose_text(), encoding="utf-8")
    executor = InterruptingExecutor()
    drill = PostgresRecoveryDrill(
        executor=executor,
        compose_file=compose_path,
        project_name="officeagent-recovery-a1b2c3d4e5f6",
        password="ephemeral-only",
        sleep=lambda _: None,
    )

    with pytest.raises(KeyboardInterrupt):
        drill.run()
    assert executor.steps[-1] == "cleanup"


class FakeExecutor:
    def __init__(
        self,
        *,
        fail_step: str | None = None,
        cleanup_failures: int = 0,
    ) -> None:
        self.fail_step = fail_step
        self.cleanup_failures = cleanup_failures
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
        assert environment["OFFICEAGENT_DRILL_PASSWORD"] == "ephemeral-only"
        if step == "cleanup" and self.cleanup_failures:
            self.cleanup_failures -= 1
            return CommandResult(returncode=1, stdout="transient teardown race")
        if step == self.fail_step:
            return CommandResult(returncode=1, stdout="must not be exposed")
        outputs = {
            "docker-endpoint": '"unix:///var/run/docker.sock"\n',
            "primary-version": "170006\n",
            "logical-marker-count": "1\n",
            "logical-schema": "document_chunks\n",
            "read-target-time": "2026-08-04T01:02:03.123456+00:00\n",
            "archive-switch": "000000010000000000000002\n",
            "archive-count": "2\n",
            "recovery-markers": "base\nbefore_target\n",
            "recovery-promoted": "f\n",
            "recovery-schema": "document_chunks\n",
            "recovery-vector-version": "0.8.6\n",
        }
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
        if step == "base-backup":
            self.steps.append(step)
            self.arguments.append(arguments)
            raise KeyboardInterrupt
        return super().execute(
            arguments,
            environment=environment,
            timeout_seconds=timeout_seconds,
            step=step,
        )


def _valid_compose_text() -> str:
    return """
services:
  primary:
    image: pgvector/pgvector:0.8.6-pg17-bookworm@sha256:fixture
    environment:
      POSTGRES_PASSWORD: ${OFFICEAGENT_DRILL_PASSWORD:?required}
    command:
      - postgres
      - -c
      - archive_mode=on
      - -c
      - wal_level=replica
      - -c
      - archive_command=test ! -f /archive/%f && cp %p /archive/%f
    volumes:
      - ./scripts/postgres_recovery_drill_init.sh:/docker-entrypoint-initdb.d/hba.sh:ro
  backup-tool:
    environment:
      PGUSER: officeagent_drill_replication
    command: [pg_basebackup]
  recovery-prepare:
    command: [touch, /recovery/recovery.signal]
  recovery:
    command: [postgres]
    healthcheck:
      test: [CMD-SHELL, "test \"$$(psql -Atqc 'SELECT pg_is_in_recovery()')\" = f"]
volumes:
  primary_data:
  wal_archive:
  base_backup:
  logical_backup:
  recovery_data:
"""
