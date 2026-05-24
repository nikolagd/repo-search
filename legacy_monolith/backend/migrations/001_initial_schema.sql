CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE IF NOT EXISTS repository (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    oai_endpoint TEXT NOT NULL,
    last_harvest TIMESTAMP WITHOUT TIME ZONE,
    refresh_interval INTEGER
);

CREATE TABLE IF NOT EXISTS author (
    id SERIAL PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    full_name TEXT,
    CONSTRAINT unique_author_full_name UNIQUE (full_name)
);

CREATE TABLE IF NOT EXISTS publication (
    id SERIAL PRIMARY KEY,
    repository_id INTEGER REFERENCES repository(id),
    title TEXT,
    abstract TEXT,
    source_url TEXT,
    embedding vector(1024),
    date TIMESTAMP WITHOUT TIME ZONE,
    oai_identifier TEXT
);

CREATE TABLE IF NOT EXISTS publication_author (
    publication_id INTEGER NOT NULL REFERENCES publication(id),
    author_id INTEGER NOT NULL REFERENCES author(id),
    PRIMARY KEY (publication_id, author_id)
);

CREATE TABLE IF NOT EXISTS admin_user (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_publication_oai_identifier
    ON publication (oai_identifier);

CREATE INDEX IF NOT EXISTS idx_publication_repository
    ON publication (repository_id);

CREATE INDEX IF NOT EXISTS idx_publication_date
    ON publication (date);

CREATE INDEX IF NOT EXISTS idx_embedding
    ON publication USING ivfflat (embedding vector_cosine_ops);
