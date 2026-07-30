"""Runtime contract for the Compose test container's credential isolation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

_SENTINEL = "OFFICEAGENT_TEST_CONTAINER_ISOLATION"
_FORBIDDEN_ENVIRONMENT_NAMES = {
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "CODEX_API_KEY",
    "CODEX_HOME",
    "OFFICEAGENT_CODEX_API_KEY",
}


@pytest.mark.skipif(
    os.environ.get(_SENTINEL) != "1",
    reason="Compose test-container isolation contract requires its test-only sentinel",
)
def test_compose_test_container_excludes_codex_node_credentials_and_mounts() -> None:
    """Assert the running test container cannot inherit host Codex state."""

    assert shutil.which("node") is None
    assert shutil.which("codex") is None

    forbidden_keys = sorted(
        key
        or key.endswith("_API_KEY")
        or key.endswith("_TOKEN")
        for key in os.environ
        if key in _FORBIDDEN_ENVIRONMENT_NAMES
        or key.startswith("CODEX_")
        or key.startswith("OPENAI_")
    )
    assert forbidden_keys == []

    assert not Path("/app/.codex").exists()
    assert not Path("/root/.codex").exists()
    mount_info = Path("/proc/self/mountinfo")
    assert mount_info.is_file()
    assert ".codex" not in mount_info.read_text(encoding="utf-8").casefold()
