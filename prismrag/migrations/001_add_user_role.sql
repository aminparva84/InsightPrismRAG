-- Migration 001: add role column to user_account
-- Roles: user (default) | admin (tenant admin) | superadmin (platform admin)
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';

DO $$
BEGIN
    ALTER TABLE prismrag.user_account
        ADD CONSTRAINT ck_user_role CHECK (role IN ('user', 'admin', 'superadmin'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Add data_region to tenant table (referenced in admin UI)
ALTER TABLE prismrag.tenant ADD COLUMN IF NOT EXISTS data_region VARCHAR(30) NOT NULL DEFAULT 'eastus2';

-- Add last_login_at to user_account (used by admin user list)
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
