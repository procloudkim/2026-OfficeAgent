"""Required ingestion path: upload -> extraction -> persistence."""

from __future__ import annotations

from dataclasses import replace

from httpx import ASGITransport, AsyncClient

from officeagent.api.app import create_app
from officeagent.application.chunking import ChunkTokenizationError
from tests.helpers import build_test_runtime, make_text_pdf


async def test_uploads_txt_and_pdf_then_persists_searchable_chunks(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    app = create_app(runtime.services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            text_response = await client.post(
                "/v1/documents",
                files={
                    "file": (
                        "benefits.txt",
                        b"Education benefit is two million won per year.",
                        "text/plain",
                    )
                },
            )
            pdf_response = await client.post(
                "/v1/documents",
                files={
                    "file": (
                        "deployment.pdf",
                        make_text_pdf("Deployment requires two reviewers."),
                        "application/pdf",
                    )
                },
            )

    assert text_response.status_code == 201
    assert pdf_response.status_code == 201
    assert text_response.json()["change"] == "created"
    assert pdf_response.json()["change"] == "created"
    assert text_response.json()["chunk_count"] >= 1
    assert pdf_response.json()["chunk_count"] >= 1

    persisted = await runtime.metadata.get_document(
        text_response.json()["document_id"]
    )
    assert persisted is not None
    assert persisted.filename == "benefits.txt"
    education_hits = await runtime.vectors.search(
        await runtime.embeddings.embed_query("education"),
        limit=2,
    )
    deployment_hits = await runtime.vectors.search(
        await runtime.embeddings.embed_query("deployment"),
        limit=2,
    )
    assert education_hits[0].chunk.filename == "benefits.txt"
    assert deployment_hits[0].chunk.filename == "deployment.pdf"


async def test_upload_rejects_unsupported_empty_and_oversized_files(
    test_data_dir,
) -> None:
    runtime = await build_test_runtime(test_data_dir)
    app = create_app(runtime.services)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            unsupported = await client.post(
                "/v1/documents",
                files={"file": ("policy.docx", b"not-docx", "application/octet-stream")},
            )
            empty = await client.post(
                "/v1/documents",
                files={"file": ("empty.txt", b"", "text/plain")},
            )
            oversized = await client.post(
                "/v1/documents",
                files={"file": ("large.txt", b"x" * 1_000_001, "text/plain")},
            )

    assert unsupported.status_code == 415
    assert empty.status_code == 422
    assert oversized.status_code == 413


async def test_upload_hides_internal_chunk_tokenizer_failure(test_data_dir) -> None:
    class FailingIngestion:
        max_upload_bytes = 1_000_000

        async def ingest(self, **kwargs):
            del kwargs
            raise ChunkTokenizationError("private/tokenizer/cache/path")

    runtime = await build_test_runtime(test_data_dir)
    app = create_app(replace(runtime.services, ingestion=FailingIngestion()))

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/documents",
                files={"file": ("policy.txt", b"searchable", "text/plain")},
            )

    assert response.status_code == 502
    assert response.json() == {"detail": "Document processing failed"}
    assert "private" not in response.text
