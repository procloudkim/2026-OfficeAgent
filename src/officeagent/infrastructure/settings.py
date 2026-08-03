"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_READINESS_TIMEOUT_SECONDS = 30.0


class Settings(BaseSettings):
    """Runtime choices with conservative local-development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="OFFICEAGENT_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    data_dir: Path = Path(".data")
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    sqlite_filename: str = "officeagent.sqlite3"
    chroma_directory_name: str = "chroma"
    chroma_collection: str = "document_chunks_v1"
    database_url: str = (
        "postgresql://officeagent:officeagent_dev_only@127.0.0.1:15432/officeagent"
    )
    database_admin_url: str | None = None
    readiness_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=MAX_READINESS_TIMEOUT_SECONDS,
    )
    readiness_instance_nonce: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    chunk_size: int = Field(default=900, ge=200)
    chunk_overlap: int = Field(default=150, ge=0)
    retrieval_limit: int = Field(default=5, ge=1, le=20)
    minimum_relevance_score: float = Field(default=0.30, ge=-1.0, le=1.0)
    cache_similarity_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    prompt_input_token_budget: int = Field(default=4096, ge=512, le=65_536)

    codex_executable: str = "codex"
    codex_workspace_directory_name: str = "codex-workspace"
    codex_max_concurrent_requests: int = Field(default=2, ge=1, le=16)
    codex_notification_queue_size: int = Field(default=100, ge=10, le=10_000)
    codex_initialize_timeout_seconds: float = Field(default=10.0, gt=0)
    codex_thread_timeout_seconds: float = Field(default=10.0, gt=0)
    codex_first_delta_timeout_seconds: float = Field(default=45.0, gt=0)
    codex_overall_timeout_seconds: float = Field(default=120.0, gt=0)

    @model_validator(mode="after")
    def validate_chunk_window(self) -> Settings:
        """Reject an overlap that cannot advance the chunk cursor."""

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self

    @property
    def sqlite_path(self) -> Path:
        """Path of the local metadata and cache database."""

        return self.data_dir / self.sqlite_filename

    @property
    def chroma_path(self) -> Path:
        """Directory of the persistent vector database."""

        return self.data_dir / self.chroma_directory_name

    @property
    def codex_workspace_path(self) -> Path:
        """Dedicated empty working directory exposed to the Codex thread."""

        return self.data_dir / self.codex_workspace_directory_name


@lru_cache
def get_settings() -> Settings:
    """Return one process-wide settings object."""

    return Settings()
