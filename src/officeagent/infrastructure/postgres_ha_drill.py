"""Disposable local PostgreSQL 17 asynchronous streaming-standby drill.

This opt-in drill proves a narrow same-Docker-backend sequence: asynchronous
WAL replay, an explicit old-primary fence, read-write route rejection before a
manual promotion, and a write through the same route after promotion.  It does
not implement automatic failover, quorum, production HA, zero-loss, or an
RPO/RTO guarantee.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from officeagent.infrastructure.migrations import MigrationError, discover_migrations
from officeagent.infrastructure.postgres_recovery_drill import (
    CommandExecutor,
    CommandResult,
    SubprocessExecutor,
    validate_local_docker_endpoint,
)
from officeagent.infrastructure.postgres_recovery_drill import (
    DrillError as HaDrillError,
)

POSTGRES_MAJOR = 17
DEFAULT_COMPOSE_FILE = Path(__file__).resolve().parents[3] / "compose.ha-local.yaml"
PROJECT_NAME = re.compile(r"officeagent-ha-[0-9a-f]{12}")
EPHEMERAL_PASSWORD = re.compile(r"[A-Za-z0-9_-]{12,128}")
MIGRATION_VERSION = re.compile(r"\d{4}")
MIGRATION_MANIFEST_ROW = re.compile(r"\d{4}:[0-9a-f]{64}")
WAL_LSN = re.compile(r"[0-9A-F]+/[0-9A-F]+")

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


@dataclass(frozen=True)
class HaDrillObservation:
    """Bounded proofs required before a sanitized PASS receipt can be issued."""

    postgres_major: int
    migration_head: str
    primary_migrations_verified: bool
    standby_migrations_verified: bool
    replication_state: str
    synchronization_state: str
    standby_was_in_recovery: bool
    marker_replayed: bool
    replay_lsn_reached: bool
    pre_fence_route_node: str
    old_primary_stopped: bool
    standby_readable_after_fence: bool
    standby_passfile_on_tmpfs: bool
    route_rejected_before_promotion: bool
    any_fallback_route_verified: bool
    promotion_succeeded: bool
    post_promotion_route_node: str
    post_promotion_write_count: int
    old_primary_remained_stopped: bool


def build_ha_environment(source: Mapping[str, str], *, password: str) -> dict[str, str]:
    """Build a narrow child environment containing only one ephemeral secret."""

    if EPHEMERAL_PASSWORD.fullmatch(password) is None:
        raise HaDrillError("ephemeral HA drill password is invalid")
    if source.get("DOCKER_HOST", "").strip():
        raise HaDrillError("DOCKER_HOST override is not allowed")
    environment = {key: source[key] for key in _ENVIRONMENT_ALLOWLIST if source.get(key)}
    environment["OFFICEAGENT_HA_PASSWORD"] = password
    return environment


def validate_ha_compose_definition(compose_text: str) -> None:
    """Reject unsafe or semantically incomplete changes to the fixed topology."""

    required_tokens = {
        "pgvector/pgvector:0.8.6-pg17-bookworm@sha256:": "pinned PostgreSQL image",
        "${OFFICEAGENT_HA_PASSWORD:?required}": "ephemeral password",
        "wal_level=replica": "streaming WAL configuration",
        "hot_standby=on": "hot standby configuration",
        "cluster_name=primary": "primary identity",
        "cluster_name=standby": "standby identity",
        "--command 'SELECT 1'": "target database readiness probe",
        "pg_basebackup": "physical standby initialization",
        "standby.signal": "standby signal",
        "passfile=/run/officeagent-ha/.pgpass": "runtime replication passfile",
        "/run/officeagent-ha:rw,noexec,nosuid,nodev": "standby credential tmpfs",
        "PGHOST: primary,standby": "fixed multi-host route",
        "PGPORT: 5432,5432": "fixed multi-host ports",
        "PGTARGETSESSIONATTRS: read-write": "read-write target routing",
        'PGCONNECT_TIMEOUT: "2"': "bounded route timeout",
        "dockerfile: Dockerfile.test": "locked application image",
        'command: ["officeagent-migrate"]': "application migration runner",
        "OFFICEAGENT_DATABASE_URL: postgresql://postgres:"
        "${OFFICEAGENT_HA_PASSWORD:?required}@primary:5432/officeagent_ha": (
            "isolated migration database URL"
        ),
        "internal: true": "internal Docker network",
        "primary_data:": "primary data volume",
        "standby_data:": "standby data volume",
    }
    for token, label in required_tokens.items():
        if token not in compose_text:
            raise HaDrillError(f"HA Compose definition is missing {label}")

    if re.search(r"(?m)^\s*ports\s*:", compose_text):
        raise HaDrillError("HA Compose definition must not publish host ports")
    if re.search(r"(?m)^\s*restart\s*:", compose_text):
        raise HaDrillError("HA Compose definition must not set a restart policy")
    if "$${PGDATA}/.pgpass" in compose_text or "/var/lib/postgresql/data/.pgpass" in compose_text:
        raise HaDrillError("HA Compose definition must not persist a passfile in PGDATA")

    forbidden_tokens = {
        "network_mode: host": "host networking",
        "/var/run/docker.sock": "Docker socket mount",
        "OPENAI_API_KEY": "LLM credentials",
        "CODEX_HOME": "Codex credentials",
        "patroni": "automatic failover",
        "repmgr": "automatic failover",
        "pg_auto_failover": "automatic failover",
    }
    lowered = compose_text.casefold()
    for token, label in forbidden_tokens.items():
        if token.casefold() in lowered:
            raise HaDrillError(f"HA Compose definition contains {label}")


def build_ha_receipt(observation: HaDrillObservation) -> dict[str, object]:
    """Validate all safety gates and return a credential-free bounded receipt."""

    if observation.postgres_major != POSTGRES_MAJOR:
        raise HaDrillError("HA drill did not run on PostgreSQL 17")
    if MIGRATION_VERSION.fullmatch(observation.migration_head) is None:
        raise HaDrillError("migration head proof is invalid")
    if not (
        observation.primary_migrations_verified
        and observation.standby_migrations_verified
    ):
        raise HaDrillError("migration checksum proof is incomplete")
    if observation.replication_state != "streaming":
        raise HaDrillError("standby never reached streaming replication")
    if observation.synchronization_state != "async":
        raise HaDrillError("replication was not explicitly asynchronous")
    if not observation.standby_was_in_recovery:
        raise HaDrillError("standby recovery proof is missing")
    if not observation.marker_replayed:
        raise HaDrillError("streamed marker proof is missing")
    if not observation.replay_lsn_reached:
        raise HaDrillError("standby replay LSN proof is missing")
    if observation.pre_fence_route_node != "primary":
        raise HaDrillError("pre-fence route did not select the primary")
    if not observation.old_primary_stopped:
        raise HaDrillError("old primary was not fenced")
    if not observation.standby_readable_after_fence:
        raise HaDrillError("standby was not readable after the fence")
    if not observation.standby_passfile_on_tmpfs:
        raise HaDrillError("standby replication passfile was not confined to tmpfs")
    if not observation.route_rejected_before_promotion:
        raise HaDrillError("read-write route was not rejected before promotion")
    if not observation.any_fallback_route_verified:
        raise HaDrillError("same-route any fallback proof is missing")
    if not observation.promotion_succeeded:
        raise HaDrillError("manual promotion proof is missing")
    if observation.post_promotion_route_node != "standby":
        raise HaDrillError("same route did not select the promoted standby")
    if observation.post_promotion_write_count != 1:
        raise HaDrillError("post-promotion write proof is incomplete")
    if not observation.old_primary_remained_stopped:
        raise HaDrillError("old primary did not remain fenced")

    return {
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
        "postgres_major": observation.postgres_major,
        "migration": {
            "head": observation.migration_head,
            "checksums_verified_on_both_nodes": True,
        },
        "replication": {
            "state": observation.replication_state,
            "synchronization": observation.synchronization_state,
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
            "same_route_selected": observation.post_promotion_route_node,
            "post_promotion_write": True,
            "old_primary_remained_stopped": True,
        },
        "cleanup": "completed_before_receipt",
    }


class PostgresHaDrill:
    """Orchestrate a manual failover proof in a disposable Compose project."""

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
        self._environment = build_ha_environment(
            source_environment if source_environment is not None else os.environ,
            password=password,
        )
        self._sleep = sleep

    def run(self) -> dict[str, object]:
        """Run, fence, clean all local resources, and only then issue PASS."""

        self._validate_inputs()
        endpoint = self._execute(
            ("docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"),
            step="docker-endpoint",
        )
        validate_local_docker_endpoint(endpoint.stdout.strip())

        observation: HaDrillObservation | None = None
        failure: BaseException | None = None
        try:
            observation = self._run_probes()
        except BaseException as error:  # cleanup also runs for user interruption
            failure = error

        try:
            self._cleanup()
        except HaDrillError as cleanup_error:
            if failure is not None:
                if isinstance(failure, HaDrillError):
                    raise HaDrillError(f"{failure}; {cleanup_error}") from failure
                raise HaDrillError(f"unexpected HA drill failure; {cleanup_error}") from failure
            raise cleanup_error

        if failure is not None:
            raise failure
        if observation is None:  # pragma: no cover - defensive invariant
            raise HaDrillError("HA drill produced no observation")
        return build_ha_receipt(observation)

    def _validate_inputs(self) -> None:
        if PROJECT_NAME.fullmatch(self._project_name) is None:
            raise HaDrillError("disposable HA Compose project name is invalid")
        try:
            compose_text = self._compose_file.read_text(encoding="utf-8")
        except OSError as error:
            raise HaDrillError("HA drill Compose file is unavailable") from error
        validate_ha_compose_definition(compose_text)

    def _cleanup(self) -> None:
        fence_failure: BaseException | None = None
        fence_returncode = 0
        try:
            fence_result = self._execute(
                self._compose("stop", "--timeout", "30", "primary", "standby"),
                step="cleanup-fence",
                timeout_seconds=60,
                allow_failure=True,
            )
            fence_returncode = fence_result.returncode
        except BaseException as error:
            fence_failure = error
            fence_returncode = 1

        command = self._compose(
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
        )
        last_error: BaseException | None = fence_failure
        for attempt in range(3):
            down_succeeded = False
            try:
                result = self._execute(
                    command,
                    step="cleanup",
                    timeout_seconds=120,
                    allow_failure=True,
                )
                down_succeeded = result.returncode == 0
            except BaseException as error:
                last_error = error

            try:
                self._assert_cleanup_complete()
            except BaseException as error:
                last_error = error
            else:
                if down_succeeded:
                    if fence_failure is not None and not isinstance(
                        fence_failure, HaDrillError
                    ):
                        raise fence_failure
                    return
            if attempt < 2:
                self._sleep(1)
        fence_note = " after cleanup fence failed" if fence_returncode else ""
        raise HaDrillError(
            f"cleanup failed for disposable project {self._project_name}{fence_note}"
        ) from last_error

    def _assert_cleanup_complete(self) -> None:
        checks = (
            (
                "cleanup-containers",
                (
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self._project_name}",
                ),
            ),
            (
                "cleanup-volumes",
                (
                    "docker",
                    "volume",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self._project_name}",
                ),
            ),
            (
                "cleanup-image",
                (
                    "docker",
                    "image",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"reference={self._project_name}-migration",
                ),
            ),
            (
                "cleanup-network",
                (
                    "docker",
                    "network",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self._project_name}",
                ),
            ),
        )
        residue_found = False
        for step, arguments in checks:
            result = self._execute(arguments, step=step, timeout_seconds=30)
            residue_found = residue_found or bool(result.stdout.strip())
        if residue_found:
            raise HaDrillError(
                f"cleanup left resources for disposable project {self._project_name}"
            )

    def _run_probes(self) -> HaDrillObservation:
        try:
            migrations = discover_migrations()
        except MigrationError as error:
            raise HaDrillError("repository migrations are invalid") from error
        expected_migrations = tuple(
            f"{migration.version}:{migration.checksum}" for migration in migrations
        )

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
        raw_version = self._single_line(
            self._psql("primary", "SHOW server_version_num", step="primary-version").stdout,
            step="primary-version",
        )
        postgres_major = self._parse_integer(raw_version, step="primary-version") // 10_000

        self._execute(
            self._compose("run", "--build", "--rm", "--no-deps", "migration"),
            step="apply-schema",
            timeout_seconds=300,
        )
        primary_migrations = self._query_migration_manifest(
            "primary", step="primary-migrations"
        )
        if primary_migrations != expected_migrations:
            raise HaDrillError("primary migration ledger does not match source")

        self._psql(
            "primary",
            "CREATE TABLE ha_drill_markers ("
            "sequence smallint PRIMARY KEY, marker text UNIQUE NOT NULL)",
            step="create-marker-table",
        )
        self._execute(
            self._compose("run", "--rm", "--no-deps", "standby-init"),
            step="init-standby",
            timeout_seconds=180,
        )
        self._execute(
            self._compose("up", "--detach", "--wait", "--wait-timeout", "90", "standby"),
            step="start-standby",
            timeout_seconds=120,
        )
        self._execute(
            self._compose(
                "exec",
                "-T",
                "standby",
                "/bin/sh",
                "-ceu",
                'test ! -e "$PGDATA/.pgpass"; '
                "test -f /run/officeagent-ha/.pgpass; "
                "test \"$(stat -c '%a:%U' /run/officeagent-ha/.pgpass)\" "
                '= "600:postgres"',
            ),
            step="standby-credential-storage",
            timeout_seconds=30,
        )
        standby_passfile_on_tmpfs = True
        standby_in_recovery = self._single_line(
            self._psql(
                "standby",
                "SELECT pg_is_in_recovery()",
                step="standby-recovery-state",
            ).stdout,
            step="standby-recovery-state",
        )
        if standby_in_recovery not in {"t", "f"}:
            raise HaDrillError("standby-recovery-state returned an invalid value")
        if standby_in_recovery != "t":
            raise HaDrillError("standby was not in recovery before the fence")

        replication_state, synchronization_state = self._wait_for_replication()
        self._psql(
            "primary",
            "INSERT INTO ha_drill_markers VALUES (1, 'streamed_before_fence')",
            step="insert-stream-marker",
        )
        marker_lsn = self._single_line(
            self._psql(
                "primary",
                "SELECT pg_current_wal_flush_lsn()",
                step="primary-marker-lsn",
            ).stdout,
            step="primary-marker-lsn",
        )
        if WAL_LSN.fullmatch(marker_lsn) is None:
            raise HaDrillError("primary-marker-lsn returned an invalid value")
        marker_replayed, replay_lsn_reached = self._wait_for_replay(marker_lsn)

        pre_fence_route_node = self._single_line(
            self._route_psql(
                "SELECT current_setting('cluster_name')",
                step="pre-fence-route",
            ).stdout,
            step="pre-fence-route",
        )
        if pre_fence_route_node != "primary":
            raise HaDrillError("pre-fence route did not select the primary")
        self._execute(
            self._compose("stop", "--timeout", "30", "primary"),
            step="stop-primary",
            timeout_seconds=60,
        )
        old_primary_stopped = self._service_is_stopped(step="primary-fenced")

        direct_standby = self._single_line(
            self._psql(
                "standby",
                "SELECT pg_is_in_recovery(), EXISTS ("
                "SELECT 1 FROM ha_drill_markers WHERE marker = 'streamed_before_fence')",
                step="standby-readable-after-fence",
            ).stdout,
            step="standby-readable-after-fence",
        )
        standby_readable_after_fence = direct_standby == "t|t"
        if not standby_readable_after_fence:
            raise HaDrillError("standby was not readable after the fence")

        rejected_route = self._route_psql(
            "SELECT current_setting('cluster_name')",
            step="pre-promotion-route",
            allow_failure=True,
        )
        if rejected_route.returncode == 0:
            raise HaDrillError("pre-promotion route accepted a read-write target")
        fallback_route = self._single_line(
            self._route_psql(
                "SELECT current_setting('cluster_name'), pg_is_in_recovery()",
                step="pre-promotion-read-only-route",
                allow_read_only=True,
            ).stdout,
            step="pre-promotion-read-only-route",
        )
        any_fallback_route_verified = fallback_route == "standby|t"
        if not any_fallback_route_verified:
            raise HaDrillError(
                "same-route fallback route did not select the recovering standby"
            )

        promoted = self._single_line(
            self._psql(
                "standby",
                "SELECT pg_promote(true, 60)",
                step="promote-standby",
                timeout_seconds=75,
            ).stdout,
            step="promote-standby",
        )
        if promoted != "t":
            raise HaDrillError("manual promotion was not accepted")
        promotion_succeeded = self._wait_for_promotion()

        post_route = self._single_line(
            self._route_psql(
                "WITH inserted AS ("
                "INSERT INTO ha_drill_markers VALUES (2, 'after_promotion') RETURNING 1"
                ") SELECT current_setting('cluster_name'), count(*) FROM inserted",
                step="post-promotion-route",
            ).stdout,
            step="post-promotion-route",
        )
        post_route_parts = post_route.split("|")
        if len(post_route_parts) != 2:
            raise HaDrillError("post-promotion-route returned an invalid value")
        post_promotion_route_node = post_route_parts[0]
        post_promotion_write_count = self._parse_integer(
            post_route_parts[1], step="post-promotion-route"
        )
        if post_promotion_route_node != "standby":
            raise HaDrillError("same route did not select the promoted standby")
        if post_promotion_write_count != 1:
            raise HaDrillError("post-promotion route did not perform exactly one write")
        direct_post_count = self._query_integer(
            "standby",
            "SELECT count(*) FROM ha_drill_markers WHERE marker = 'after_promotion'",
            step="post-promotion-marker",
        )
        if direct_post_count != post_promotion_write_count:
            raise HaDrillError("post-promotion write was not durable on standby")

        standby_migrations = self._query_migration_manifest(
            "standby", step="standby-migrations"
        )
        if standby_migrations != expected_migrations:
            raise HaDrillError("standby migration ledger does not match source")
        old_primary_remained_stopped = self._service_is_stopped(
            step="primary-remains-fenced"
        )

        return HaDrillObservation(
            postgres_major=postgres_major,
            migration_head=migrations[-1].version,
            primary_migrations_verified=True,
            standby_migrations_verified=True,
            replication_state=replication_state,
            synchronization_state=synchronization_state,
            standby_was_in_recovery=standby_in_recovery == "t",
            marker_replayed=marker_replayed,
            replay_lsn_reached=replay_lsn_reached,
            pre_fence_route_node=pre_fence_route_node,
            old_primary_stopped=old_primary_stopped,
            standby_readable_after_fence=standby_readable_after_fence,
            standby_passfile_on_tmpfs=standby_passfile_on_tmpfs,
            route_rejected_before_promotion=True,
            any_fallback_route_verified=any_fallback_route_verified,
            promotion_succeeded=promotion_succeeded,
            post_promotion_route_node=post_promotion_route_node,
            post_promotion_write_count=post_promotion_write_count,
            old_primary_remained_stopped=old_primary_remained_stopped,
        )

    def _wait_for_replication(self) -> tuple[str, str]:
        sql = (
            "SELECT state, sync_state FROM pg_stat_replication "
            "WHERE application_name = 'officeagent_ha_standby'"
        )
        for _ in range(60):
            raw_value = self._psql(
                "primary", sql, step="replication-status"
            ).stdout.strip()
            if raw_value == "streaming|async":
                return ("streaming", "async")
            self._sleep(1)
        raise HaDrillError("replication-status did not reach streaming|async")

    def _wait_for_replay(self, marker_lsn: str) -> tuple[bool, bool]:
        sql = (
            "SELECT EXISTS (SELECT 1 FROM ha_drill_markers "
            "WHERE marker = 'streamed_before_fence') "
            f"AND pg_last_wal_replay_lsn() >= '{marker_lsn}'::pg_lsn"
        )
        for _ in range(60):
            raw_value = self._psql(
                "standby", sql, step="standby-replay"
            ).stdout.strip()
            if raw_value == "t":
                return (True, True)
            if raw_value not in {"", "f"}:
                raise HaDrillError("standby-replay returned an invalid value")
            self._sleep(1)
        raise HaDrillError("standby did not replay the marker LSN")

    def _wait_for_promotion(self) -> bool:
        for _ in range(60):
            raw_value = self._psql(
                "standby",
                "SELECT pg_is_in_recovery()",
                step="standby-promoted",
            ).stdout.strip()
            if raw_value == "f":
                return True
            if raw_value != "t":
                raise HaDrillError("standby-promoted returned an invalid value")
            self._sleep(1)
        raise HaDrillError("standby did not leave recovery after promotion")

    def _service_is_stopped(self, *, step: str) -> bool:
        result = self._execute(
            self._compose("ps", "--status", "running", "--quiet", "primary"),
            step=step,
        )
        if result.stdout.strip():
            raise HaDrillError(f"{step} found the old primary running")
        return True

    def _query_integer(self, service: str, sql: str, *, step: str) -> int:
        raw_value = self._single_line(
            self._psql(service, sql, step=step).stdout,
            step=step,
        )
        return self._parse_integer(raw_value, step=step)

    def _query_migration_manifest(self, service: str, *, step: str) -> tuple[str, ...]:
        raw_value = self._psql(
            service,
            "SELECT version || ':' || checksum "
            "FROM schema_migrations ORDER BY version",
            step=step,
        ).stdout
        rows = tuple(raw_value.splitlines())
        if not rows or any(MIGRATION_MANIFEST_ROW.fullmatch(row) is None for row in rows):
            raise HaDrillError(f"{step} returned an invalid migration manifest")
        return rows

    def _psql(
        self,
        service: str,
        sql: str,
        *,
        step: str,
        timeout_seconds: float = 60,
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
                "officeagent_ha",
                "--tuples-only",
                "--no-align",
                "--command",
                sql,
            ),
            step=step,
            timeout_seconds=timeout_seconds,
        )

    def _route_psql(
        self,
        sql: str,
        *,
        step: str,
        allow_failure: bool = False,
        allow_read_only: bool = False,
    ) -> CommandResult:
        run_arguments = ["run", "--rm", "--no-deps"]
        if allow_read_only:
            run_arguments.extend(("--env", "PGTARGETSESSIONATTRS=any"))
        run_arguments.extend(("route-probe", "--command", sql))
        return self._execute(
            self._compose(*run_arguments),
            step=step,
            timeout_seconds=30,
            allow_failure=allow_failure,
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
            raise HaDrillError(f"{step} failed")
        return result

    @staticmethod
    def _single_line(raw_value: str, *, step: str) -> str:
        value = raw_value.strip()
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise HaDrillError(f"{step} returned an invalid value")
        return value

    @staticmethod
    def _parse_integer(raw_value: str, *, step: str) -> int:
        if re.fullmatch(r"\d{1,12}", raw_value) is None:
            raise HaDrillError(f"{step} returned an invalid integer")
        return int(raw_value)


def _new_project_name() -> str:
    return f"officeagent-ha-{secrets.token_hex(6)}"


def run_ha_drill(compose_file: Path = DEFAULT_COMPOSE_FILE) -> dict[str, object]:
    """Run the fixed local topology using a process-local random identity."""

    drill = PostgresHaDrill(
        executor=SubprocessExecutor(),
        compose_file=compose_file,
        project_name=_new_project_name(),
        password=secrets.token_urlsafe(32),
    )
    return drill.run()


def main(argv: list[str] | None = None) -> int:
    """Print only a sanitized JSON receipt or sanitized failure."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        receipt = run_ha_drill()
    except KeyboardInterrupt:
        print("HA drill interrupted", file=sys.stderr)
        return 130
    except HaDrillError as error:
        print(f"HA drill failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("HA drill failed: unexpected internal failure", file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
