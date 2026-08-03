"""Disposable PostgreSQL 17 logical-restore and PITR verification drill.

The two recovery mechanisms are deliberately reported separately.  A
``pg_dump``/``pg_restore`` round trip proves logical restore capability; only a
physical base backup followed by archived-WAL replay is called PITR.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from officeagent.infrastructure.migrations import (
    MigrationError,
    MigrationFile,
    discover_migrations,
)

POSTGRES_MAJOR = 17
DEFAULT_COMPOSE_FILE = Path(__file__).resolve().parents[3] / "compose.recovery-drill.yaml"
PROJECT_NAME = re.compile(r"officeagent-recovery-[0-9a-f]{12}")
EPHEMERAL_PASSWORD = re.compile(r"[A-Za-z0-9_-]{12,128}")
WAL_FILE_NAME = re.compile(r"[0-9A-F]{24}")
TARGET_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00"
)
VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
MIGRATION_VERSION = re.compile(r"\d{4}")
MIGRATION_MANIFEST_ROW = re.compile(r"\d{4}:[0-9a-f]{64}")

_ENVIRONMENT_ALLOWLIST = (
    "PATH",
    "HOME",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "XDG_RUNTIME_DIR",
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)


class DrillError(RuntimeError):
    """A sanitized, fail-closed recovery-drill failure."""


@dataclass(frozen=True)
class CommandResult:
    """The only child-process information consumed by the drill."""

    returncode: int
    stdout: str


class CommandExecutor(Protocol):
    """Injectable command boundary used by offline contract tests."""

    def execute(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        timeout_seconds: float,
        step: str,
    ) -> CommandResult: ...


class SubprocessExecutor:
    """Run fixed argv commands without a host shell or inherited credentials."""

    def execute(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        timeout_seconds: float,
        step: str,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DrillError(f"{step} could not run") from error
        return CommandResult(returncode=completed.returncode, stdout=completed.stdout)


@dataclass(frozen=True)
class DrillObservation:
    """Bounded facts required to issue a sanitized PASS receipt."""

    postgres_major: int
    vector_version: str
    migration_head: str
    logical_marker_count: int
    logical_schema_present: bool
    logical_migrations_verified: bool
    base_backup_manifest_verified: bool
    wal_archive_count: int
    recovered_markers: tuple[str, ...]
    recovery_promoted: bool
    recovery_signal_removed: bool
    recovered_schema_present: bool
    recovered_migrations_verified: bool


def build_drill_environment(source: Mapping[str, str], *, password: str) -> dict[str, str]:
    """Create a narrow environment instead of forwarding product credentials."""

    if EPHEMERAL_PASSWORD.fullmatch(password) is None:
        raise DrillError("ephemeral drill password is invalid")
    if source.get("DOCKER_HOST", "").strip():
        raise DrillError("DOCKER_HOST override is not allowed")
    environment = {key: source[key] for key in _ENVIRONMENT_ALLOWLIST if source.get(key)}
    environment["OFFICEAGENT_DRILL_PASSWORD"] = password
    return environment


def validate_local_docker_endpoint(raw_json: str) -> str:
    """Accept only a local Unix-domain Docker endpoint."""

    try:
        endpoint = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as error:
        raise DrillError("Docker must use a local Unix socket") from error
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("unix:///")
        or any(character in endpoint for character in ("\n", "\r", "\x00"))
    ):
        raise DrillError("Docker must use a local Unix socket")
    return endpoint


def validate_compose_definition(compose_text: str) -> None:
    """Catch accidental drift in the repository-owned default Compose definition."""

    required_tokens = {
        "pgvector/pgvector:0.8.6-pg17-bookworm@sha256:": "pinned PostgreSQL image",
        "${OFFICEAGENT_DRILL_PASSWORD:?required}": "ephemeral password",
        "archive_mode=on": "archive_mode",
        "wal_level=replica": "wal_level",
        "archive_command=": "archive_command",
        "pg_basebackup": "pg_basebackup",
        "recovery.signal": "recovery.signal",
        "pg_is_in_recovery()": "writable recovery healthcheck",
        "postgres_recovery_drill_init.sh": "replication HBA bootstrap",
        "dockerfile: Dockerfile.test": "application migration image",
        'command: ["officeagent-migrate"]': "application migration runner",
        "OFFICEAGENT_DATABASE_URL: postgresql://postgres:"
        "${OFFICEAGENT_DRILL_PASSWORD:?required}@primary:5432/officeagent_drill": (
            "isolated migration database URL"
        ),
        "PGUSER: officeagent_drill_replication": "dedicated replication role",
        "primary_data:": "primary data volume",
        "wal_archive:": "WAL archive volume",
        "base_backup:": "base backup volume",
        "recovery_data:": "recovery data volume",
    }
    for token, label in required_tokens.items():
        if token not in compose_text:
            raise DrillError(f"compose definition is missing {label}")
    if re.search(r"(?m)^\s*ports\s*:", compose_text):
        raise DrillError("compose definition must not publish host ports")
    forbidden_tokens = {
        "network_mode: host": "host networking",
        "/var/run/docker.sock": "Docker socket mount",
        "OPENAI_API_KEY": "LLM credentials",
    }
    for token, label in forbidden_tokens.items():
        if token in compose_text:
            raise DrillError(f"compose definition contains {label}")


def build_recovery_receipt(observation: DrillObservation) -> dict[str, object]:
    """Validate every proof and return a credential-free receipt."""

    if observation.postgres_major != POSTGRES_MAJOR:
        raise DrillError("PITR drill did not run on PostgreSQL 17")
    if MIGRATION_VERSION.fullmatch(observation.migration_head) is None:
        raise DrillError("migration head proof is invalid")
    if (
        observation.logical_marker_count != 1
        or not observation.logical_schema_present
        or not observation.logical_migrations_verified
    ):
        raise DrillError("logical restore proof is incomplete")
    if not observation.base_backup_manifest_verified:
        raise DrillError("physical base backup verification failed")
    if observation.wal_archive_count < 1:
        raise DrillError("WAL archive proof is missing")
    if observation.recovered_markers != ("base", "before_target"):
        raise DrillError("recovery target boundary was not preserved")
    if not observation.recovery_promoted:
        raise DrillError("recovered server did not promote")
    if not observation.recovery_signal_removed:
        raise DrillError("recovery.signal was not removed after promotion")
    if (
        not observation.recovered_schema_present
        or not observation.recovered_migrations_verified
    ):
        raise DrillError("recovered schema proof is missing")
    if VERSION.fullmatch(observation.vector_version) is None:
        raise DrillError("recovered vector extension version is invalid")

    return {
        "schema_version": 1,
        "status": "PASS",
        "topology": "local_disposable_docker",
        "postgres_major": observation.postgres_major,
        "vector_version": observation.vector_version,
        "logical_restore": {
            "classification": "logical_snapshot_restore",
            "method": "pg_dump_custom_pg_restore",
            "pitr": False,
            "marker_count": observation.logical_marker_count,
            "schema_present": observation.logical_schema_present,
            "migration_head": observation.migration_head,
            "migration_checksums_verified": observation.logical_migrations_verified,
        },
        "pitr": {
            "classification": "physical_base_backup_wal_replay",
            "method": "pg_basebackup_recovery_target_time",
            "base_backup_manifest_verified": observation.base_backup_manifest_verified,
            "wal_archive_count": observation.wal_archive_count,
            "before_target_present": True,
            "after_target_absent": True,
            "promoted": observation.recovery_promoted,
            "recovery_signal_removed": observation.recovery_signal_removed,
            "schema_present": observation.recovered_schema_present,
            "migration_head": observation.migration_head,
            "migration_checksums_verified": observation.recovered_migrations_verified,
        },
        "cleanup": "completed_before_receipt",
    }


class PostgresRecoveryDrill:
    """Orchestrate logical restore and physical PITR in isolated Compose volumes."""

    def __init__(
        self,
        *,
        executor: CommandExecutor,
        compose_file: Path,
        project_name: str,
        password: str,
        sleep: Callable[[float], None] = time.sleep,
        source_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._executor = executor
        self._compose_file = compose_file.resolve()
        self._project_name = project_name
        self._environment = build_drill_environment(
            source_environment if source_environment is not None else os.environ,
            password=password,
        )
        self._sleep = sleep

    def run(self) -> dict[str, object]:
        """Run the drill, clean every project volume, then issue a PASS receipt."""

        self._validate_inputs()
        endpoint = self._execute(
            ("docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"),
            step="docker-endpoint",
        )
        validate_local_docker_endpoint(endpoint.stdout.strip())

        observation: DrillObservation | None = None
        failure: BaseException | None = None
        try:
            observation = self._run_probes()
        except BaseException as error:  # cleanup also runs for user interruption
            failure = error

        try:
            self._cleanup()
        except DrillError as cleanup_error:
            if failure is not None:
                if isinstance(failure, DrillError):
                    raise DrillError(f"{failure}; {cleanup_error}") from failure
                raise DrillError(f"unexpected drill failure; {cleanup_error}") from failure
            raise cleanup_error

        if failure is not None:
            raise failure
        if observation is None:  # pragma: no cover - defensive invariant
            raise DrillError("drill produced no observation")
        return build_recovery_receipt(observation)

    def _cleanup(self) -> None:
        command = self._compose(
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
        )
        last_error: DrillError | None = None
        for attempt in range(3):
            try:
                result = self._execute(
                    command,
                    step="cleanup",
                    timeout_seconds=120,
                    allow_failure=True,
                )
            except DrillError as error:
                last_error = error
            else:
                if result.returncode == 0:
                    return
            if attempt < 2:
                self._sleep(1)
        raise DrillError(
            f"cleanup failed for disposable project {self._project_name}"
        ) from last_error

    def _validate_inputs(self) -> None:
        if PROJECT_NAME.fullmatch(self._project_name) is None:
            raise DrillError("disposable Compose project name is invalid")
        try:
            compose_text = self._compose_file.read_text(encoding="utf-8")
        except OSError as error:
            raise DrillError("recovery drill compose file is unavailable") from error
        validate_compose_definition(compose_text)

    def _run_probes(self) -> DrillObservation:
        self._execute(
            self._compose("run", "--rm", "--no-deps", "volume-init"),
            step="init-volumes",
            timeout_seconds=120,
        )
        self._execute(
            self._compose("up", "--detach", "--wait", "--wait-timeout", "90", "primary"),
            step="start-primary",
            timeout_seconds=120,
        )
        postgres_version = self._single_line(
            self._psql(
                "primary",
                "officeagent_drill",
                "SHOW server_version_num",
                step="primary-version",
            ).stdout,
            step="primary-version",
        )
        postgres_major = self._parse_integer(postgres_version, step="primary-version") // 10_000

        try:
            migrations = discover_migrations()
        except MigrationError as error:
            raise DrillError("repository migrations are invalid") from error
        expected_migrations = self._expected_migration_manifest(migrations)
        self._execute(
            self._compose(
                "run",
                "--build",
                "--rm",
                "--no-deps",
                "migration",
            ),
            step="apply-schema",
            timeout_seconds=300,
        )
        primary_migrations = self._query_migration_manifest(
            "primary",
            "officeagent_drill",
            step="primary-migrations",
        )
        if primary_migrations != expected_migrations:
            raise DrillError("primary migration ledger does not match source")
        self._psql(
            "primary",
            "officeagent_drill",
            "CREATE TABLE recovery_drill_markers "
            "(sequence smallint PRIMARY KEY, marker text UNIQUE NOT NULL); "
            "INSERT INTO recovery_drill_markers VALUES (1, 'base')",
            step="insert-base",
        )
        self._execute(
            self._compose(
                "exec",
                "-T",
                "primary",
                "pg_dump",
                "--username",
                "postgres",
                "--dbname",
                "officeagent_drill",
                "--format",
                "custom",
                "--file",
                "/logical/officeagent.dump",
            ),
            step="logical-dump",
        )
        self._execute(
            self._compose(
                "exec",
                "-T",
                "primary",
                "createdb",
                "--username",
                "postgres",
                "--template",
                "template0",
                "officeagent_logical_restore",
            ),
            step="logical-create-database",
        )
        self._execute(
            self._compose(
                "exec",
                "-T",
                "primary",
                "pg_restore",
                "--username",
                "postgres",
                "--dbname",
                "officeagent_logical_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "/logical/officeagent.dump",
            ),
            step="logical-restore",
            timeout_seconds=120,
        )
        logical_marker_count = self._query_integer(
            "primary",
            "officeagent_logical_restore",
            "SELECT count(*) FROM recovery_drill_markers WHERE marker = 'base'",
            step="logical-marker-count",
        )
        logical_schema_present = self._query_relation(
            "primary",
            "officeagent_logical_restore",
            step="logical-schema",
        )
        logical_migrations = self._query_migration_manifest(
            "primary",
            "officeagent_logical_restore",
            step="logical-migrations",
        )
        if logical_migrations != expected_migrations:
            raise DrillError("logical restore migration ledger does not match source")

        self._execute(
            self._compose(
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "pg_basebackup",
                "backup-tool",
                "--host",
                "primary",
                "--username",
                "officeagent_drill_replication",
                "--pgdata",
                "/basebackup/data",
                "--format",
                "plain",
                "--wal-method",
                "stream",
                "--checkpoint",
                "fast",
                "--label",
                "officeagent-pitr-drill",
            ),
            step="base-backup",
            timeout_seconds=180,
        )
        self._execute(
            self._compose(
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "pg_verifybackup",
                "backup-tool",
                "--exit-on-error",
                "/basebackup/data",
            ),
            step="base-verify",
            timeout_seconds=120,
        )
        self._psql(
            "primary",
            "officeagent_drill",
            "INSERT INTO recovery_drill_markers VALUES (2, 'before_target')",
            step="insert-before",
        )
        target_time = self._single_line(
            self._psql(
                "primary",
                "officeagent_drill",
                "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', "
                "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')",
                step="read-target-time",
            ).stdout,
            step="read-target-time",
        )
        if TARGET_TIME.fullmatch(target_time) is None:
            raise DrillError("read-target-time returned an invalid value")
        self._psql(
            "primary",
            "officeagent_drill",
            "SELECT pg_sleep(0.05); "
            "INSERT INTO recovery_drill_markers VALUES (3, 'after_target')",
            step="insert-after",
        )
        wal_file = self._single_line(
            self._psql(
                "primary",
                "officeagent_drill",
                "SELECT pg_walfile_name(pg_switch_wal())",
                step="archive-switch",
            ).stdout,
            step="archive-switch",
        )
        if WAL_FILE_NAME.fullmatch(wal_file) is None:
            raise DrillError("archive-switch returned an invalid WAL file name")
        self._wait_for_archive(wal_file)
        wal_archive_count = self._query_integer(
            "primary",
            "officeagent_drill",
            "SELECT archived_count FROM pg_stat_archiver",
            step="archive-count",
        )
        self._execute(
            self._compose("stop", "--timeout", "30", "primary"),
            step="stop-primary",
            timeout_seconds=60,
        )
        self._execute(
            self._compose(
                "run",
                "--rm",
                "--no-deps",
                "--env",
                f"RECOVERY_TARGET_TIME={target_time}",
                "recovery-prepare",
            ),
            step="prepare-recovery",
            timeout_seconds=120,
        )
        self._execute(
            self._compose("up", "--detach", "--wait", "--wait-timeout", "90", "recovery"),
            step="start-recovery",
            timeout_seconds=120,
        )
        recovered_markers = tuple(
            line
            for line in self._psql(
                "recovery",
                "officeagent_drill",
                "SELECT marker FROM recovery_drill_markers ORDER BY sequence",
                step="recovery-markers",
            ).stdout.splitlines()
            if line
        )
        promoted_value = self._single_line(
            self._psql(
                "recovery",
                "officeagent_drill",
                "SELECT pg_is_in_recovery()",
                step="recovery-promoted",
            ).stdout,
            step="recovery-promoted",
        )
        if promoted_value not in {"t", "f"}:
            raise DrillError("recovery-promoted returned an invalid value")
        self._execute(
            self._compose(
                "exec",
                "-T",
                "recovery",
                "test",
                "!",
                "-e",
                "/var/lib/postgresql/data/recovery.signal",
            ),
            step="recovery-signal",
        )
        recovered_schema_present = self._query_relation(
            "recovery",
            "officeagent_drill",
            step="recovery-schema",
        )
        recovered_migrations = self._query_migration_manifest(
            "recovery",
            "officeagent_drill",
            step="recovery-migrations",
        )
        if recovered_migrations != expected_migrations:
            raise DrillError("recovered migration ledger does not match source")
        vector_version = self._single_line(
            self._psql(
                "recovery",
                "officeagent_drill",
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
                step="recovery-vector-version",
            ).stdout,
            step="recovery-vector-version",
        )

        return DrillObservation(
            postgres_major=postgres_major,
            vector_version=vector_version,
            migration_head=migrations[-1].version,
            logical_marker_count=logical_marker_count,
            logical_schema_present=logical_schema_present,
            logical_migrations_verified=True,
            base_backup_manifest_verified=True,
            wal_archive_count=wal_archive_count,
            recovered_markers=recovered_markers,
            recovery_promoted=promoted_value == "f",
            recovery_signal_removed=True,
            recovered_schema_present=recovered_schema_present,
            recovered_migrations_verified=True,
        )

    def _wait_for_archive(self, wal_file: str) -> None:
        for _ in range(60):
            result = self._execute(
                self._compose(
                    "exec",
                    "-T",
                    "primary",
                    "test",
                    "-s",
                    f"/archive/{wal_file}",
                ),
                step="archive-ready",
                allow_failure=True,
            )
            if result.returncode == 0:
                return
            self._sleep(1)
        raise DrillError("WAL archive did not become ready")

    def _query_integer(self, service: str, database: str, sql: str, *, step: str) -> int:
        raw_value = self._single_line(
            self._psql(service, database, sql, step=step).stdout,
            step=step,
        )
        return self._parse_integer(raw_value, step=step)

    def _query_relation(self, service: str, database: str, *, step: str) -> bool:
        relation = self._single_line(
            self._psql(
                service,
                database,
                "SELECT to_regclass('public.document_chunks') IS NOT NULL "
                "AND EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'documents' "
                "AND column_name = 'chunking_version'"
                ")",
                step=step,
            ).stdout,
            step=step,
        )
        if relation not in {"t", "f"}:
            raise DrillError(f"{step} returned an invalid schema proof")
        return relation == "t"

    def _query_migration_manifest(
        self,
        service: str,
        database: str,
        *,
        step: str,
    ) -> tuple[str, ...]:
        raw_value = self._psql(
            service,
            database,
            "SELECT version || ':' || checksum "
            "FROM schema_migrations ORDER BY version",
            step=step,
        ).stdout
        rows = tuple(raw_value.splitlines())
        if not rows or any(MIGRATION_MANIFEST_ROW.fullmatch(row) is None for row in rows):
            raise DrillError(f"{step} returned an invalid migration manifest")
        return rows

    @staticmethod
    def _expected_migration_manifest(
        migrations: tuple[MigrationFile, ...],
    ) -> tuple[str, ...]:
        return tuple(
            f"{migration.version}:{migration.checksum}" for migration in migrations
        )

    def _psql(
        self,
        service: str,
        database: str,
        sql: str,
        *,
        step: str,
    ) -> CommandResult:
        return self._execute(
            self._compose(
                "exec",
                "-T",
                service,
                "psql",
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--username",
                "postgres",
                "--dbname",
                database,
                "--tuples-only",
                "--no-align",
                "--command",
                sql,
            ),
            step=step,
        )

    def _compose(self, *arguments: str) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "--file",
            str(self._compose_file),
            "--project-name",
            self._project_name,
            *arguments,
        )

    def _execute(
        self,
        arguments: tuple[str, ...],
        *,
        step: str,
        timeout_seconds: float = 60,
        allow_failure: bool = False,
    ) -> CommandResult:
        result = self._executor.execute(
            arguments,
            environment=self._environment,
            timeout_seconds=timeout_seconds,
            step=step,
        )
        if result.returncode != 0 and not allow_failure:
            raise DrillError(f"{step} failed")
        return result

    @staticmethod
    def _single_line(raw_value: str, *, step: str) -> str:
        value = raw_value.strip()
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise DrillError(f"{step} returned an invalid value")
        return value

    @staticmethod
    def _parse_integer(raw_value: str, *, step: str) -> int:
        if re.fullmatch(r"\d{1,12}", raw_value) is None:
            raise DrillError(f"{step} returned an invalid integer")
        return int(raw_value)


def _new_project_name() -> str:
    return f"officeagent-recovery-{secrets.token_hex(6)}"


def run_recovery_drill(compose_file: Path = DEFAULT_COMPOSE_FILE) -> dict[str, object]:
    """Run the default local drill with process-local ephemeral identity."""

    drill = PostgresRecoveryDrill(
        executor=SubprocessExecutor(),
        compose_file=compose_file,
        project_name=_new_project_name(),
        password=secrets.token_urlsafe(32),
    )
    return drill.run()


def main(argv: list[str] | None = None) -> int:
    """Print a sanitized JSON receipt or a sanitized error message."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        receipt = run_recovery_drill()
    except DrillError as error:
        print(f"recovery drill failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
