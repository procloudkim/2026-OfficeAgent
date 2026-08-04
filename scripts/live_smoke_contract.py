"""Pure validation for the subscription-backed live-smoke receipt."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

EXPECTED_READINESS = {
    "status": "ready",
    "migration_head": "0003",
    "vector_version": "0.8.6",
}

_CODEX_VERSION_PATTERN = re.compile(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
_EVENT_NAMES = frozenset({"answer.started", "answer.delta", "answer.citation", "answer.completed"})
_SOURCE_FIELDS = (
    "document_id",
    "document_version",
    "filename",
    "chunk_id",
    "chunk_index",
    "page_number",
    "start_offset",
    "end_offset",
)

Event = tuple[str, Mapping[str, Any]]


def build_readiness_receipt(
    *,
    http_status: int,
    readiness: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and sanitize the database-backed HTTP readiness result."""

    if type(http_status) is not int or http_status != 200:
        raise RuntimeError("Live HTTP readiness did not return status 200")
    if dict(readiness) != EXPECTED_READINESS:
        raise RuntimeError("Live HTTP readiness did not match the expected V2 database state")
    return {
        "command_status": "PASS",
        "http_status": http_status,
        "migration_head": readiness["migration_head"],
        "status": readiness["status"],
        "vector_version": readiness["vector_version"],
    }


def build_live_receipt(
    *,
    readiness: Mapping[str, Any],
    created_upload: Mapping[str, Any],
    updated_upload: Mapping[str, Any],
    initial_events: Sequence[Event],
    cached_events: Sequence[Event],
    updated_events: Sequence[Event],
    document_name: str,
    codex_version: str,
) -> dict[str, Any]:
    """Validate the G7 runtime transitions and return a sanitized receipt."""

    if dict(readiness) != EXPECTED_READINESS:
        raise RuntimeError("Live HTTP readiness did not match the expected V2 database state")
    validate_codex_version(codex_version)
    document_id = _validate_upload_transition(created_upload, updated_upload)

    phase_contracts = (
        (
            "initial_miss",
            "initial cache miss",
            initial_events,
            False,
            None,
            1,
            2,
        ),
        ("exact_hit", "exact cache hit", cached_events, True, "exact", 1, 1),
        (
            "update_miss",
            "miss after update",
            updated_events,
            False,
            None,
            2,
            2,
        ),
    )
    phases = {
        key: _validate_answer_phase(
            events=events,
            phase=label,
            expected_cache_hit=cache_hit,
            expected_cache_match=cache_match,
            expected_document_id=document_id,
            expected_document_version=document_version,
            expected_filename=document_name,
            minimum_delta_count=minimum_delta_count,
        )
        for (
            key,
            label,
            events,
            cache_hit,
            cache_match,
            document_version,
            minimum_delta_count,
        ) in phase_contracts
    }
    return {
        "status": "ok",
        "transport": "uvicorn-http-sse",
        "database_isolation": "dedicated-disposable",
        "codex_version": codex_version,
        "readiness": dict(readiness),
        "document_id": document_id,
        "document_versions": {"created": 1, "updated": 2},
        "cache_sequence": ["miss", "exact_hit", "miss_after_update"],
        "phases": phases,
    }


def parse_codex_version(output: bytes) -> str:
    """Normalize only the credential-free, single-line Codex version format."""

    if len(output) > 128:
        raise RuntimeError("Codex version output was not in the expected format")
    try:
        version = output.decode("ascii").strip()
    except UnicodeDecodeError:
        raise RuntimeError("Codex version output was not in the expected format") from None
    return validate_codex_version(version)


def validate_codex_version(version: str) -> str:
    """Reject arbitrary command output while preserving the normalized version."""

    if _CODEX_VERSION_PATTERN.fullmatch(version) is None:
        raise RuntimeError("Codex version output was not in the expected format")
    return version


def _validate_upload_transition(
    created: Mapping[str, Any],
    updated: Mapping[str, Any],
) -> str:
    document_id = created.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise RuntimeError("Live document create response did not include an identifier")
    if not _contains_metadata(
        created,
        {
            "change": "created",
            "document_version": 1,
            "invalidated_cache_entries": 0,
        },
    ):
        raise RuntimeError("Live document create did not start from an empty database")
    if not _contains_metadata(
        updated,
        {
            "document_id": document_id,
            "change": "updated",
            "document_version": 2,
        },
    ):
        raise RuntimeError("Live document update did not produce version two")
    if updated.get("invalidated_cache_entries") != 1:
        raise RuntimeError("Live document update did not invalidate exactly one cache entry")
    return document_id


def _validate_answer_phase(
    *,
    events: Sequence[Event],
    phase: str,
    expected_cache_hit: bool,
    expected_cache_match: str | None,
    expected_document_id: str,
    expected_document_version: int,
    expected_filename: str,
    minimum_delta_count: int,
) -> dict[str, Any]:
    event_names = [name for name, _ in events]
    if (
        not event_names
        or event_names[0] != "answer.started"
        or event_names.count("answer.started") != 1
    ):
        raise RuntimeError(f"Live {phase} did not start with exactly one answer.started event")
    if event_names[-1] != "answer.completed" or event_names.count("answer.completed") != 1:
        raise RuntimeError(f"Live {phase} did not end with exactly one answer.completed event")
    if any(name not in _EVENT_NAMES for name in event_names):
        raise RuntimeError(f"Live {phase} contained an unexpected SSE event")

    delta_indices = [index for index, name in enumerate(event_names) if name == "answer.delta"]
    deltas = [events[index][1].get("delta") for index in delta_indices]
    if len(deltas) < minimum_delta_count or any(
        not isinstance(delta, str) or not delta for delta in deltas
    ):
        requirement = "at least two genuine deltas" if minimum_delta_count == 2 else "a delta"
        raise RuntimeError(f"Expected {requirement} for live {phase}")

    citation_indices = [
        index for index, name in enumerate(event_names) if name == "answer.citation"
    ]
    if not citation_indices or min(citation_indices) <= max(delta_indices):
        raise RuntimeError(f"Live {phase} citation did not follow the answer deltas")

    request_id = events[0][1].get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or any(payload.get("request_id") != request_id for _, payload in events)
    ):
        raise RuntimeError(f"Live {phase} SSE events did not share one request_id")

    expected_started = {
        "cache_hit": expected_cache_hit,
        "cache_match": expected_cache_match,
    }
    expected_completed = {
        **expected_started,
        "grounded": True,
        "cache_written": not expected_cache_hit,
        "cache_skip_reason": "cache_hit" if expected_cache_hit else None,
    }
    if not _contains_metadata(events[0][1], expected_started) or not _contains_metadata(
        events[-1][1],
        expected_completed,
    ):
        raise RuntimeError(f"Live {phase} cache metadata did not match its contract")

    return {
        **expected_completed,
        "delta_count": len(deltas),
        "first_delta_before_completed": True,
        "citations": _citation_receipts(
            events,
            phase=phase,
            expected_document_id=expected_document_id,
            expected_document_version=expected_document_version,
            expected_filename=expected_filename,
        ),
    }


def _citation_receipts(
    events: Sequence[Event],
    *,
    phase: str,
    expected_document_id: str,
    expected_document_version: int,
    expected_filename: str,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    expected_source = {
        "document_id": expected_document_id,
        "document_version": expected_document_version,
        "filename": expected_filename,
    }
    for name, payload in events:
        if name != "answer.citation":
            continue
        if not _contains_metadata(payload, expected_source) or not _valid_citation(payload):
            raise RuntimeError(f"Live {phase} citation did not identify its source chunk")
        receipts.append({field: payload.get(field) for field in _SOURCE_FIELDS})
    if not receipts:
        raise RuntimeError(f"Live {phase} citation was missing")
    return receipts


def _valid_citation(payload: Mapping[str, Any]) -> bool:
    if any(field not in payload for field in _SOURCE_FIELDS):
        return False
    chunk_id = payload.get("chunk_id")
    chunk_index = payload.get("chunk_index")
    page_number = payload.get("page_number")
    start_offset = payload.get("start_offset")
    end_offset = payload.get("end_offset")
    quote = payload.get("quote")
    score = payload.get("score")
    return (
        isinstance(chunk_id, str)
        and bool(chunk_id)
        and _integer_at_least(chunk_index, 0)
        and (page_number is None or _integer_at_least(page_number, 1))
        and _integer_at_least(start_offset, 0)
        and _integer_at_least(end_offset, 1)
        and start_offset < end_offset
        and isinstance(quote, str)
        and bool(quote.strip())
        and type(score) in (int, float)
        and math.isfinite(score)
    )


def _integer_at_least(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _contains_metadata(
    payload: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return all(key in payload and payload[key] == value for key, value in expected.items())
