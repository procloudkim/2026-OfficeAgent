ALTER TABLE documents
ADD COLUMN indexing_version text NOT NULL
DEFAULT 'legacy-document-index-v1:unknown-settings';

ALTER TABLE documents
ALTER COLUMN indexing_version DROP DEFAULT;

ALTER TABLE documents
ADD CONSTRAINT documents_indexing_version_not_blank
CHECK (length(btrim(indexing_version)) > 0);
