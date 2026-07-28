"""Cross-platform test fixtures."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


@pytest.fixture
def test_data_dir() -> Path:
    """Use a normal workspace directory; Windows sandboxes can reject pytest's 0700 temp."""

    directory = Path(".test-data") / uuid.uuid4().hex
    directory.mkdir(parents=True)
    return directory
