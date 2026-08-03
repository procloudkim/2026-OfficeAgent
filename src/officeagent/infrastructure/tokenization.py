"""Untruncated token-count proxy for bounded application prompts."""

from __future__ import annotations

import anyio
import tiktoken
from tiktoken import Encoding, get_encoding

TOKENIZER_ENCODING = "o200k_base"
TOKENIZER_POLICY_VERSION = "ordinary-v1"

# Compatibility aliases retained for callers of the original prompt-only API.
PROMPT_TOKENIZER_ENCODING = TOKENIZER_ENCODING
PROMPT_TOKENIZER_POLICY_VERSION = TOKENIZER_POLICY_VERSION


class TiktokenTokenCounter:
    """Count or bound text with one named, untruncated OpenAI BPE proxy."""

    def __init__(self) -> None:
        self._encoding: Encoding | None = None

    @property
    def token_counter_version(self) -> str:
        """Bind cache identity to package, encoding, and special-token policy."""

        return (
            f"tiktoken:{tiktoken.__version__}:encoding={TOKENIZER_ENCODING}:"
            f"policy={TOKENIZER_POLICY_VERSION}"
        )

    async def count_tokens(self, text: str) -> int:
        """Count one full string without model-context truncation."""

        return await anyio.to_thread.run_sync(self.count_tokens_sync, text)

    def count_tokens_sync(self, text: str) -> int:
        """Count a string synchronously for ingestion preparation."""

        return len(self._get_encoding().encode_ordinary(text))

    def bounded_prefix(self, text: str, *, max_tokens: int) -> str:
        """Return the longest tokenizer prefix that is also valid Unicode text."""

        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        encoding = self._get_encoding()
        tokens = encoding.encode_ordinary(text)
        if len(tokens) <= max_tokens:
            return text
        prefix_bytes = encoding.decode_bytes(tokens[:max_tokens])
        try:
            prefix = prefix_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.end != len(prefix_bytes):
                raise ValueError("token prefix contains invalid internal UTF-8") from error
            prefix = prefix_bytes[: error.start].decode("utf-8")
        while prefix and len(encoding.encode_ordinary(prefix)) > max_tokens:
            prefix = prefix[:-1]
        return prefix

    def _get_encoding(self) -> Encoding:
        if self._encoding is None:
            self._encoding = get_encoding(TOKENIZER_ENCODING)
        return self._encoding


TiktokenPromptTokenCounter = TiktokenTokenCounter
