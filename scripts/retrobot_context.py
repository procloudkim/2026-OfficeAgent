"""Extract one bounded worklog section for Retrobot's external prompt."""

from __future__ import annotations

import io
import re
import sys
import unicodedata
from collections.abc import Iterable
from typing import BinaryIO

MAX_SOURCE_BYTES = 256 * 1024
MAX_SECTION_BYTES = 8 * 1024
_DATED_H2 = re.compile(r"^## \d{4}-\d{2}-\d{2} — \S")


class RetrobotContextError(RuntimeError):
    """A worklog cannot be reduced to one safe bounded section."""


def extract_latest_worklog_section(raw: bytes) -> bytes:
    """Return the last dated H2 and stop before the next H2."""

    return _extract_latest_worklog_section(io.BytesIO(raw))


def _extract_latest_worklog_section(lines: Iterable[bytes]) -> bytes:
    candidate = bytearray()
    capturing = False
    found = False
    overflowed = False
    source_bytes = 0

    for raw_line in lines:
        source_bytes += len(raw_line)
        if source_bytes > MAX_SOURCE_BYTES:
            raise RetrobotContextError("Worklog source exceeds the local read limit")
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            raise RetrobotContextError("Worklog is not valid UTF-8") from None
        if "\r" in line or any(
            unicodedata.category(character).startswith("C") and character not in "\n\t"
            for character in line
        ):
            raise RetrobotContextError("Worklog contains unsafe control characters")

        is_h2 = line.startswith("## ")
        if is_h2 and _DATED_H2.match(line):
            candidate = bytearray(raw_line)
            capturing = True
            found = True
            overflowed = len(candidate) > MAX_SECTION_BYTES
            continue
        if is_h2 and capturing:
            capturing = False
            continue
        if capturing and not overflowed:
            candidate.extend(raw_line)
            overflowed = len(candidate) > MAX_SECTION_BYTES

    if not found or not candidate:
        raise RetrobotContextError("Worklog has no dated H2 section")
    if overflowed:
        raise RetrobotContextError("Latest worklog section exceeds 8 KiB")
    return bytes(candidate)


def main(stream: BinaryIO | None = None) -> int:
    """Read a committed blob from stdin and emit only its latest bounded section."""

    source = stream or sys.stdin.buffer
    try:
        section = _extract_latest_worklog_section(source)
    except RetrobotContextError:
        print("Retrobot context rejected the committed worklog.", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
