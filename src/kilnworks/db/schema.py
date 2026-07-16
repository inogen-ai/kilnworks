def schema_sql(dimensions: int) -> str:
    """Build the schema DDL with the given embedding vector width.

    `dimensions` is coerced to `int` and interpolated directly into the DDL;
    callers must only pass trusted values (e.g. from settings), never raw
    user input.
    """
    dimensions = int(dimensions)
    return f"""
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    source_uri TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    acl_tags TEXT[] NOT NULL DEFAULT '{{public}}',
    metadata JSONB NOT NULL DEFAULT '{{}}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent upgrade for installs created before `metadata` existed; existing
-- docs carry an empty object and populate their metadata on re-ingest.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}';

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INT NOT NULL,
    text TEXT NOT NULL,
    heading_path TEXT[] NOT NULL DEFAULT '{{}}',
    acl_tags TEXT[] NOT NULL DEFAULT '{{public}}',
    page INTEGER,
    embedding vector({dimensions}) NOT NULL
);
-- Idempotent upgrade for installs created before `page` existed; the column is
-- nullable and existing docs need re-ingesting to populate it.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page INTEGER;

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_acl_idx ON chunks USING gin (acl_tags);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    display_name TEXT NOT NULL DEFAULT '',
    principals TEXT[] NOT NULL DEFAULT '{{public}}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

CREATE TABLE IF NOT EXISTS jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{{}}',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    error TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status, id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS created_by TEXT;
"""


SCHEMA_SQL = schema_sql(1536)
