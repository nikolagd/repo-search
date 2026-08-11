ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS received_records INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS parsed_records INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS skipped_records INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS deleted_records INTEGER;

ALTER TABLE admin_job
    ADD COLUMN IF NOT EXISTS pages_processed INTEGER;
