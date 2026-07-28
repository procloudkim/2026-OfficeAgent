"""Composition root for production adapters."""

from __future__ import annotations

from officeagent.api.app import ServiceContainer
from officeagent.application.ingestion import IngestionService
from officeagent.application.question_answering import QuestionAnsweringService
from officeagent.infrastructure.chroma_store import ChromaVectorStore
from officeagent.infrastructure.codex_app_server import CodexAppServerGateway
from officeagent.infrastructure.embeddings import FastEmbedEmbeddingProvider
from officeagent.infrastructure.settings import Settings, get_settings
from officeagent.infrastructure.sqlite_store import SqliteMetadataStore


async def build_services(settings: Settings | None = None) -> ServiceContainer:
    """Initialize local persistence and wire concrete production dependencies."""

    active_settings = settings or get_settings()
    active_settings.data_dir.mkdir(parents=True, exist_ok=True)
    active_settings.codex_workspace_path.mkdir(parents=True, exist_ok=True)

    metadata = SqliteMetadataStore(active_settings.sqlite_path)
    await metadata.initialize()
    vectors = ChromaVectorStore(
        active_settings.chroma_path,
        active_settings.chroma_collection,
    )
    embeddings = FastEmbedEmbeddingProvider(
        active_settings.embedding_model,
        active_settings.embedding_dimension,
    )
    llm = CodexAppServerGateway(
        active_settings,
        repository_root=active_settings.codex_workspace_path,
    )
    return ServiceContainer(
        ingestion=IngestionService(
            metadata,
            vectors,
            embeddings,
            chunk_size=active_settings.chunk_size,
            chunk_overlap=active_settings.chunk_overlap,
            max_upload_bytes=active_settings.max_upload_bytes,
        ),
        questions=QuestionAnsweringService(
            metadata,
            vectors,
            embeddings,
            llm,
            retrieval_limit=active_settings.retrieval_limit,
            minimum_relevance_score=active_settings.minimum_relevance_score,
            cache_similarity_threshold=active_settings.cache_similarity_threshold,
        ),
    )
