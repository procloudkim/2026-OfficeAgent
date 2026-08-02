"""Runtime contract for the Compose test container's credential isolation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from officeagent.infrastructure.test_runner import (
    TEST_CONTAINER_SENTINEL,
    find_forbidden_environment_names,
    replace_database_name,
    validate_isolated_database_url,
    validate_test_container_environment,
    validate_test_database_environment,
)

_SENTINEL = TEST_CONTAINER_SENTINEL


def test_isolation_predicates_fail_closed_without_disclosing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = {
        "API_KEY",
        "TOKEN",
        "PATH",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "OPENAI_ORG_ID",
    }

    assert find_forbidden_environment_names(names) == (
        "ANTHROPIC_API_KEY",
        "API_KEY",
        "GITHUB_TOKEN",
        "OPENAI_ORG_ID",
        "TOKEN",
    )
    with pytest.raises(RuntimeError, match="sentinel"):
        validate_test_container_environment({"PATH": "/usr/bin"})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY") as exc_info:
        validate_test_container_environment(
            {_SENTINEL: "1", "ANTHROPIC_API_KEY": "not-logged"}
        )
    assert "not-logged" not in str(exc_info.value)
    validate_test_container_environment({_SENTINEL: "1", "PATH": "/usr/bin"})

    validate_isolated_database_url(
        "postgresql://officeagent:not-logged@db:5432/officeagent_test_012345abcdef"
    )
    for unsafe_connection in (
        "",
        "not-a-connection-string",
        "postgresql://officeagent:not-logged@db:5432/officeagent",
        "postgresql://officeagent:not-logged@db:5432/officeagent_test_short",
    ):
        with pytest.raises(RuntimeError, match="isolated") as connection_error:
            validate_isolated_database_url(unsafe_connection)
        assert "not-logged" not in str(connection_error.value)
    with pytest.raises(RuntimeError, match="isolated"):
        validate_isolated_database_url(
            "postgresql://officeagent:not-logged@db:5432/"
            "officeagent_test_012345abcdef?dbname=production"
        )
    replaced_url = replace_database_name(
        "postgresql://officeagent:not-logged@db:5432/postgres?dbname=production",
        "officeagent_test_012345abcdef",
    )
    assert conninfo_to_dict(replaced_url)["dbname"] == "officeagent_test_012345abcdef"
    with pytest.raises(RuntimeError, match="isolated"):
        validate_test_database_environment(
            {
                _SENTINEL: "1",
                "OFFICEAGENT_DATABASE_URL": (
                    "postgresql://officeagent:not-logged@db:5432/officeagent"
                ),
            }
        )

    def parser_must_not_run(_: str) -> dict[str, str]:
        raise AssertionError("oversized conninfo reached the parser")

    monkeypatch.setattr(
        "officeagent.infrastructure.test_runner.conninfo_to_dict",
        parser_must_not_run,
    )
    with pytest.raises(RuntimeError, match="isolated"):
        validate_isolated_database_url("x" * 4_097)


@pytest.mark.skipif(
    os.environ.get(_SENTINEL) != "1",
    reason="Compose test-container isolation contract requires its test-only sentinel",
)
def test_compose_test_container_excludes_codex_node_credentials_and_mounts() -> None:
    """Assert the running test container cannot inherit host Codex state."""

    assert shutil.which("node") is None
    assert shutil.which("codex") is None

    forbidden_keys = find_forbidden_environment_names(os.environ)
    assert not forbidden_keys

    assert not Path("/app/.codex").exists()
    assert not Path("/root/.codex").exists()
    mount_info = Path("/proc/self/mountinfo")
    assert mount_info.is_file()
    assert ".codex" not in mount_info.read_text(encoding="utf-8").casefold()
