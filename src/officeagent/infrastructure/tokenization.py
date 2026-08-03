"""Untruncated token-count proxy for bounded application prompts."""

from __future__ import annotations

import anyio
import tiktoken
from tiktoken import Encoding, get_encoding

PROMPT_TOKENIZER_ENCODING = "o200k_base"
PROMPT_TOKENIZER_POLICY_VERSION = "ordinary-v1"


class TiktokenPromptTokenCounter:
    """Count the complete prompt with a named OpenAI BPE encoding proxy."""

    def __init__(self) -> None:
        self._encoding: Encoding | None = None

    @property
    def token_counter_version(self) -> str:
        """Bind cache identity to package, encoding, and special-token policy."""

        return (
            f"tiktoken:{tiktoken.__version__}:encoding={PROMPT_TOKENIZER_ENCODING}:"
            f"policy={PROMPT_TOKENIZER_POLICY_VERSION}"
        )

    async def count_tokens(self, text: str) -> int:
        """Count one full string without model-context truncation."""

        return await anyio.to_thread.run_sync(self._count_tokens, text)

    def _count_tokens(self, text: str) -> int:
        return len(self._get_encoding().encode_ordinary(text))

    def _get_encoding(self) -> Encoding:
        if self._encoding is None:
            self._encoding = get_encoding(PROMPT_TOKENIZER_ENCODING)
        return self._encoding
