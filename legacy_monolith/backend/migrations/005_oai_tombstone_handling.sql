ALTER TABLE publication
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE publication
    DROP CONSTRAINT IF EXISTS publication_oai_identifier_key;

DROP INDEX IF EXISTS uq_publication_oai_identifier;

CREATE UNIQUE INDEX IF NOT EXISTS uq_publication_repository_oai_identifier
    ON publication (repository_id, oai_identifier);

CREATE TABLE IF NOT EXISTS publication_tombstone (
    repository_id INTEGER NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    oai_identifier TEXT NOT NULL,
    oai_datestamp TEXT,
    set_specs TEXT[] NOT NULL DEFAULT '{}',
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observation_count INTEGER NOT NULL DEFAULT 1,
    cleared_at TIMESTAMPTZ,
    PRIMARY KEY (repository_id, oai_identifier)
);

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS deactivated_records INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS unknown_tombstones INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS already_inactive_tombstones INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS invalid_tombstones INTEGER;
