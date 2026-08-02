"""Bounded, committed-worklog context extraction contracts."""

from __future__ import annotations

import pytest

from scripts.retrobot_context import (
    MAX_SECTION_BYTES,
    RetrobotContextError,
    extract_latest_worklog_section,
)


def test_extracts_only_the_latest_dated_h2_section() -> None:
    raw = """# Worklog

## 2026-08-01 — old

- old evidence

## 2026-08-02 — current

- current evidence

## unrelated private notes

- must not leave the machine
""".encode()

    section = extract_latest_worklog_section(raw).decode()

    assert section.startswith("## 2026-08-02 — current\n")
    assert "current evidence" in section
    assert "old evidence" not in section
    assert "unrelated private notes" not in section
    assert "must not leave" not in section


def test_later_dated_section_restarts_after_an_unrelated_h2() -> None:
    raw = """## 2026-08-01 — old
- old
## unrelated
- excluded
## 2026-08-02 — latest
- included
""".encode()

    assert extract_latest_worklog_section(raw).decode() == (
        "## 2026-08-02 — latest\n- included\n"
    )


def test_section_accepts_8192_bytes_and_rejects_8193() -> None:
    heading = "## 2026-08-02 — latest\n".encode()
    exact = heading + b"a" * (MAX_SECTION_BYTES - len(heading))

    assert len(extract_latest_worklog_section(exact)) == MAX_SECTION_BYTES
    with pytest.raises(RetrobotContextError):
        extract_latest_worklog_section(exact + b"a")


@pytest.mark.parametrize("raw", [b"", b"# no dated section\n", b"\xff"])
def test_missing_or_invalid_worklog_is_rejected(raw: bytes) -> None:
    with pytest.raises(RetrobotContextError):
        extract_latest_worklog_section(raw)
