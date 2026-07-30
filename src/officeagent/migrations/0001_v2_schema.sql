CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    document_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    normalized_key text NOT NULL,
    filename text NOT NULL,
    mime_type text NOT NULL,
    content_hash char(64) NOT NULL,
    ready boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, version)
);

CREATE UNIQUE INDEX documents_one_ready_per_key
    ON documents (normalized_key)
    WHERE ready;

CREATE UNIQUE INDEX documents_one_ready_per_id
    ON documents (document_id)
    WHERE ready;

CREATE TABLE document_chunks (
    document_id uuid NOT NULL,
    document_version integer NOT NULL,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content text NOT NULL,
    page integer CHECK (page IS NULL OR page > 0),
    start_offset integer CHECK (start_offset IS NULL OR start_offset >= 0),
    end_offset integer CHECK (
        end_offset IS NULL OR start_offset IS NULL OR end_offset >= start_offset
    ),
    embedding vector(384) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, document_version, chunk_index),
    FOREIGN KEY (document_id, document_version)
        REFERENCES documents (document_id, version)
        ON DELETE CASCADE
);

CREATE TABLE corpus_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO corpus_state (singleton, revision)
VALUES (true, 0)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE answer_cache (
    cache_id uuid PRIMARY KEY,
    normalized_question text NOT NULL,
    question_embedding vector(384) NOT NULL,
    answer text NOT NULL,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    prompt_version text NOT NULL,
    embedding_version text NOT NULL,
    retrieval_version text NOT NULL,
    created_revision bigint NOT NULL CHECK (created_revision >= 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    invalidation_reason text,
    CHECK (
        (active AND invalidated_at IS NULL AND invalidation_reason IS NULL)
        OR
        (NOT active AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX answer_cache_one_active_exact
    ON answer_cache (
        normalized_question,
        prompt_version,
        embedding_version,
        retrieval_version
    )
    WHERE active;

CREATE TABLE cache_dependencies (
    cache_id uuid NOT NULL REFERENCES answer_cache (cache_id) ON DELETE CASCADE,
    document_id uuid NOT NULL,
    document_version integer NOT NULL,
    PRIMARY KEY (cache_id, document_id, document_version),
    FOREIGN KEY (document_id, document_version)
        REFERENCES documents (document_id, version)
);

CREATE INDEX cache_dependencies_document_version
    ON cache_dependencies (document_id, document_version);
