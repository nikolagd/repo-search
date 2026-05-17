ALTER TABLE admin_user
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_admin_user_role'
    ) THEN
        ALTER TABLE admin_user
            ADD CONSTRAINT chk_admin_user_role
            CHECK (role IN ('admin', 'editor', 'viewer'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_admin_user_role
    ON admin_user (role);
