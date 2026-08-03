"""Composition root for the PostgreSQL/pgvector V2 runtime."""

from __future__ import annotations

import logging
from functools import partial

import anyio

from officeagent.api.app import ServiceContainer
from officeagent.application.observability import JsonTimingRecorder
from officeagent.application.prompts import PROMPT_TEMPLATE_VERSION
from officeagent.application.question_answering import (
    QuestionAnsweringService,
    build_retrieval_version,
)
from officeagent.application.transactional_ingestion import TransactionalIngestionService
from officeagent.domain.models import CacheVersions
from officeagent.infrastructure.codex_app_server import CodexAppServerGateway
from officeagent.infrastructure.embeddings import FastEmbedEmbeddingProvider
from officeagent.infrastructure.migrations import PostgresReadinessProbe, apply_migrations
from officeagent.infrastructure.postgres_corpus_store import PostgresCorpusStore
from officeagent.infrastructure.settings import Settings, get_settings


async def build_services(settings: Settings | None = None) -> ServiceContainer:
    """Migrate PostgreSQL and wire the V2 single-store product composition."""

    active_settings = settings or get_settings()
    active_settings.data_dir.mkdir(parents=True, exist_ok=True)
    active_settings.codex_workspace_path.mkdir(parents=True, exist_ok=True)
    if active_settings.embedding_dimension != 384:
        raise ValueError("V2 PostgreSQL schema requires embedding_dimension=384")

    await anyio.to_thread.run_sync(
        partial(apply_migrations, active_settings.database_url)
    )
    embeddings = FastEmbedEmbeddingProvider(
        active_settings.embedding_model,
        active_settings.embedding_dimension,
    )
    cache_versions = CacheVersions(
        prompt_version=PROMPT_TEMPLATE_VERSION,
        embedding_version=embeddings.embedding_version,
        retrieval_version=build_retrieval_version(
            limit=active_settings.retrieval_limit,
            minimum_score=active_settings.minimum_relevance_score,
            cache_threshold=active_settings.cache_similarity_threshold,
        ),
    )
    corpus = PostgresCorpusStore(active_settings.database_url)
    llm = CodexAppServerGateway(
        active_settings,
        repository_root=active_settings.codex_workspace_path,
    )
    timing_recorder = JsonTimingRecorder(logging.getLogger("uvicorn.error"))
    return ServiceContainer(
        ingestion=TransactionalIngestionService(
            corpus,
            embeddings,
            chunk_size=active_settings.chunk_size,
            chunk_overlap=active_settings.chunk_overlap,
            max_upload_bytes=active_settings.max_upload_bytes,
            cache_versions=cache_versions,
            retrieval_threshold=active_settings.minimum_relevance_score,
            timing_recorder=timing_recorder,
        ),
        questions=QuestionAnsweringService(
            corpus,
            corpus,
            embeddings,
            llm,
            retrieval_limit=active_settings.retrieval_limit,
            minimum_relevance_score=active_settings.minimum_relevance_score,
            cache_similarity_threshold=active_settings.cache_similarity_threshold,
            cache_versions=cache_versions,
            corpus_store=corpus,
            timing_recorder=timing_recorder,
        ),
        readiness=PostgresReadinessProbe(
            active_settings.database_url,
            timeout_seconds=active_settings.readiness_timeout_seconds,
        ),
        readiness_instance_nonce=active_settings.readiness_instance_nonce,
    )
