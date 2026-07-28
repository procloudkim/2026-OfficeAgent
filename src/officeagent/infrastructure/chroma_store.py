"""Chroma-backed persistent vector store with application-owned embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
import chromadb

from officeagent.domain.models import Chunk, SearchHit


class ChromaVectorStore:
    """Persist chunks in Chroma while keeping embedding generation outside Chroma."""

    def __init__(self, directory: Path, collection_name: str) -> None:
        self._persistent_path = directory
        self._collection_name = collection_name

    async def replace_document(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Delete all indexed versions of one document, then upsert its new chunks."""

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have matching lengths")
        if any(chunk.document_id != document_id for chunk in chunks):
            raise ValueError("every chunk must belong to document_id")
        vectors = [[float(value) for value in embedding] for embedding in embeddings]
        if any(not vector for vector in vectors):
            raise ValueError("embedding vectors must not be empty")

        await anyio.to_thread.run_sync(
            self._replace_document_sync,
            document_id,
            list(chunks),
            vectors,
        )

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[SearchHit]:
        """Search explicit query vectors and return normalized cosine scores."""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        vector = [float(value) for value in query_embedding]
        if not vector:
            raise ValueError("query_embedding must not be empty")
        return await anyio.to_thread.run_sync(self._search_sync, vector, limit)

    def _collection(self) -> Any:
        self._persistent_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self._persistent_path))
        return client.get_or_create_collection(
            name=self._collection_name,
            configuration={"hnsw": {"space": "cosine"}},
            embedding_function=None,
        )

    def _replace_document_sync(
        self,
        document_id: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        collection = self._collection()
        collection.delete(where={"document_id": document_id})
        if not chunks:
            return
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=embeddings,
            metadatas=[_metadata_from_chunk(chunk) for chunk in chunks],
        )

    def _search_sync(self, query_embedding: list[float], limit: int) -> list[SearchHit]:
        collection = self._collection()
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0] or []
        documents = result.get("documents", [[]])[0] or []
        metadatas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] or []
        hits: list[SearchHit] = []
        for chunk_id, text, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            hits.append(
                SearchHit(
                    chunk=_chunk_from_result(chunk_id, text, metadata),
                    score=1.0 - float(distance),
                )
            )
        return hits


def _metadata_from_chunk(chunk: Chunk) -> dict[str, str | int]:
    return {
        "document_id": chunk.document_id,
        "document_version": chunk.document_version,
        "filename": chunk.filename,
        "chunk_index": chunk.chunk_index,
        "page_number": chunk.page_number if chunk.page_number is not None else -1,
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
    }


def _chunk_from_result(chunk_id: str, text: str, metadata: dict[str, Any]) -> Chunk:
    page_number = int(metadata["page_number"])
    return Chunk(
        id=chunk_id,
        document_id=str(metadata["document_id"]),
        document_version=int(metadata["document_version"]),
        filename=str(metadata["filename"]),
        text=text,
        chunk_index=int(metadata["chunk_index"]),
        page_number=None if page_number == -1 else page_number,
        start_offset=int(metadata["start_offset"]),
        end_offset=int(metadata["end_offset"]),
    )
