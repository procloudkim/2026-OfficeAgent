"""FastAPI application factory and versioned routes."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from officeagent.api.schemas import (
    AnswerResponse,
    CitationResponse,
    DocumentResponse,
    QuestionRequest,
)
from officeagent.application.ingestion import DocumentTooLargeError, IngestionService
from officeagent.application.question_answering import QuestionAnsweringService
from officeagent.domain.models import AnswerEvent, AnswerEventType
from officeagent.infrastructure.parsers import EmptyDocumentError, UnsupportedDocumentError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    """Explicit application dependencies, replaceable in tests."""

    ingestion: IngestionService
    questions: QuestionAnsweringService


def create_app(services: ServiceContainer | None = None) -> FastAPI:
    """Create an app; production dependencies are initialized during lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is None:
            from officeagent.bootstrap import build_services

            app.state.services = await build_services()
        else:
            app.state.services = services
        yield

    app = FastAPI(
        title="OfficeAgent Document Q&A API",
        version="0.1.0",
        description="Document-grounded question answering with verifiable citations.",
        lifespan=lifespan,
    )

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        """Return process liveness without touching model or database state."""

        return {"status": "ok"}

    @app.post(
        "/v1/documents",
        response_model=DocumentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def upload_document(file: Annotated[UploadFile, File()]) -> DocumentResponse:
        """Extract and index one supported document."""

        ingestion = _services(app).ingestion
        content = await file.read(ingestion.max_upload_bytes + 1)
        try:
            result = await ingestion.ingest(
                filename=file.filename or "",
                content_type=file.content_type or "application/octet-stream",
                content=content,
            )
        except UnsupportedDocumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(exc),
            ) from None
        except DocumentTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=str(exc),
            ) from None
        except (EmptyDocumentError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from None

        document = result.document
        return DocumentResponse(
            document_id=document.id,
            filename=document.filename,
            content_type=document.content_type,
            content_hash=document.content_hash,
            document_version=document.version,
            change=result.change.value,
            chunk_count=result.chunk_count,
            invalidated_cache_entries=result.invalidated_cache_entries,
            updated_at=document.updated_at,
        )

    @app.post("/v1/questions", response_model=AnswerResponse, tags=["questions"])
    async def answer_question(request: QuestionRequest) -> AnswerResponse:
        """Return the same pipeline as the streaming endpoint, collected as JSON."""

        request_id = str(uuid4())
        try:
            result = await _services(app).questions.answer(request.question)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from None
        except Exception as exc:
            logger.exception("Question answering failed", extra={"request_id": request_id})
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Answer generation failed",
            ) from exc
        return AnswerResponse(
            request_id=request_id,
            answer=result.answer,
            citations=[
                CitationResponse.model_validate(citation) for citation in result.citations
            ],
            cache_hit=result.cache_hit,
            cache_match=result.cache_match,
            grounded=result.grounded,
        )

    @app.post("/v1/questions/stream", tags=["questions"])
    async def stream_answer(request: QuestionRequest) -> StreamingResponse:
        """Forward live answer deltas through an SSE event contract."""

        request_id = str(uuid4())

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in _services(app).questions.stream(request.question):
                    yield _format_sse(event, request_id)
            except Exception:
                logger.exception(
                    "Streaming question answering failed",
                    extra={"request_id": request_id},
                )
                error = AnswerEvent(
                    AnswerEventType.ERROR,
                    {
                        "code": "answer_generation_failed",
                        "message": "Answer generation failed",
                    },
                )
                yield _format_sse(error, request_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _services(app: FastAPI) -> ServiceContainer:
    try:
        return app.state.services
    except AttributeError as exc:
        raise RuntimeError("Application services were not initialized") from exc


def _format_sse(event: AnswerEvent, request_id: str) -> str:
    data: dict[str, Any] = {"request_id": request_id, **event.payload}
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.type.value}\ndata: {encoded}\n\n"
