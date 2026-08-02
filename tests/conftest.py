"""Cross-platform test fixtures."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from officeagent.infrastructure.test_runner import validate_test_database_environment


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse destructive PostgreSQL tests unless their database is disposable."""

    del session
    validate_test_database_environment(os.environ)


@pytest.fixture
def test_data_dir() -> Path:
    """Use a normal workspace directory; Windows sandboxes can reject pytest's 0700 temp."""

    directory = Path(".test-data") / uuid.uuid4().hex
    directory.mkdir(parents=True)
    return directory
