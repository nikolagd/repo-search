ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_admin_job_unacknowledged_recent
    ON admin_job (job_type, repository_id, started_at DESC)
    WHERE acknowledged_at IS NULL;
