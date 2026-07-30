"""FastEmbed adapter with separate passage and query paths."""

from __future__ import annotations

from collections.abc import Sequence

import anyio
from fastembed import TextEmbedding


class FastEmbedEmbeddingProvider:
    """Lazily load a local multilingual embedding model."""

    def __init__(self, model_name: str, dimension: int) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._model: TextEmbedding | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_version(self) -> str:
        return f"fastembed:{self._model_name}:dimension={self._dimension}"

    def _get_model(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        vectors = await anyio.to_thread.run_sync(self._embed_passages, values)
        self._validate_dimensions(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vector = await anyio.to_thread.run_sync(self._embed_one_query, text)
        self._validate_dimensions([vector])
        return vector

    def _embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._get_model().passage_embed(texts)]

    def _embed_one_query(self, text: str) -> list[float]:
        vectors = list(self._get_model().query_embed([text]))
        if len(vectors) != 1:
            raise RuntimeError("Embedding provider returned an unexpected query vector count")
        return vectors[0].tolist()

    def _validate_dimensions(self, vectors: Sequence[Sequence[float]]) -> None:
        if any(len(vector) != self._dimension for vector in vectors):
            raise RuntimeError(
                f"Embedding dimension mismatch; expected {self._dimension}"
            )
