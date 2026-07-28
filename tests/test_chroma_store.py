from officeagent.domain.models import Chunk
from officeagent.infrastructure.chroma_store import ChromaVectorStore


def _chunk(chunk_id: str, document_id: str, text: str, index: int) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id=document_id,
        document_version=1,
        filename=f"{document_id}.txt",
        text=text,
        chunk_index=index,
        page_number=None,
        start_offset=index * 10,
        end_offset=(index + 1) * 10,
    )


async def test_replace_document_replaces_old_versions_and_searches_explicit_vectors(
    test_data_dir,
) -> None:
    store = ChromaVectorStore(test_data_dir / "chroma", "chunks")
    await store.replace_document(
        "policy",
        [_chunk("policy:old", "policy", "old policy", 0)],
        [[1.0, 0.0]],
    )
    await store.replace_document(
        "policy",
        [_chunk("policy:new", "policy", "new policy", 0)],
        [[0.0, 1.0]],
    )

    hits = await store.search([0.0, 1.0], limit=3)

    assert [hit.chunk.id for hit in hits] == ["policy:new"]
    assert hits[0].chunk.page_number is None
    assert hits[0].score == 1.0
