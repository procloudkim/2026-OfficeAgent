"""Stable identities for persisted document-vector policies."""

from __future__ import annotations

import hashlib

INDEXING_POLICY_VERSION = "document-index-v1"


def build_indexing_version(
    *,
    chunking_version: str,
    embedding_version: str,
) -> str:
    """Fingerprint the policies that define every persisted document vector."""

    if not chunking_version.strip():
        raise ValueError("chunking_version must not be blank")
    if not embedding_version.strip():
        raise ValueError("embedding_version must not be blank")
    payload = (
        f"algorithm={INDEXING_POLICY_VERSION}\n"
        f"chunking={chunking_version}\n"
        f"embedding={embedding_version}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{INDEXING_POLICY_VERSION}:{digest}"
