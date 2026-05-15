CREATE TABLE IF NOT EXISTS admin_job (
    id SERIAL PRIMARY KEY,
    job_type TEXT NOT NULL,
    repository_id INTEGER REFERENCES repository(id),
    status TEXT NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    processed_records INTEGER,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_admin_job_type
        CHECK (job_type IN ('repository_harvest', 'embedding_backfill')),
    CONSTRAINT chk_admin_job_status
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT chk_admin_job_repository
        CHECK (
            (job_type = 'repository_harvest' AND repository_id IS NOT NULL)
            OR (job_type = 'embedding_backfill' AND repository_id IS NULL)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_running_repository_harvest
    ON admin_job (repository_id)
    WHERE job_type = 'repository_harvest' AND status = 'running';

CREATE UNIQUE INDEX IF NOT EXISTS uq_running_embedding_backfill
    ON admin_job (job_type)
    WHERE job_type = 'embedding_backfill' AND status = 'running';

CREATE INDEX IF NOT EXISTS idx_admin_job_recent
    ON admin_job (job_type, repository_id, started_at DESC);
