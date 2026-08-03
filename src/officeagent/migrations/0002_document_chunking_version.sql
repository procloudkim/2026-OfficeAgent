ALTER TABLE documents
ADD COLUMN chunking_version text NOT NULL
DEFAULT 'legacy-page-safe-character-v1:unknown-settings';

ALTER TABLE documents
ALTER COLUMN chunking_version DROP DEFAULT;

ALTER TABLE documents
ADD CONSTRAINT documents_chunking_version_not_blank
CHECK (length(btrim(chunking_version)) > 0);
