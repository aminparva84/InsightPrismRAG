-- PrismRAG — MFA, SCIM, organizations, regions, CMEK, email audit
-- Run after enterprise_schema.sql

-- ── Organizations (enterprise boundary for SCIM + residency) ────────────────
CREATE TABLE IF NOT EXISTS prismrag.organization (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(200) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    data_region     VARCHAR(30)  NOT NULL DEFAULT 'us-east',
    cmek_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    cmek_key_id     TEXT,
    cmek_vault_url  TEXT,
    scim_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_required    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE prismrag.tenant ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES prismrag.organization(id) ON DELETE SET NULL;
ALTER TABLE prismrag.tenant ADD COLUMN IF NOT EXISTS data_region VARCHAR(30) NOT NULL DEFAULT 'us-east';
ALTER TABLE prismrag.tenant ADD COLUMN IF NOT EXISTS cmek_key_id TEXT;
ALTER TABLE prismrag.tenant ADD COLUMN IF NOT EXISTS cmek_vault_url TEXT;

-- ── MFA on user accounts ──────────────────────────────────────────────────────
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS mfa_secret TEXT;
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES prismrag.organization(id) ON DELETE SET NULL;
ALTER TABLE prismrag.user_account ADD COLUMN IF NOT EXISTS scim_external_id VARCHAR(255);
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_scim_ext
    ON prismrag.user_account (organization_id, scim_external_id)
    WHERE scim_external_id IS NOT NULL;

-- ── SCIM bearer tokens (hashed) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.scim_token (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES prismrag.organization(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    token_prefix    VARCHAR(16) NOT NULL,
    label           VARCHAR(100) NOT NULL DEFAULT 'Default',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_scim_token_org ON prismrag.scim_token (organization_id);

-- ── SCIM groups ↔ tenants (optional mapping) ────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.scim_group (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES prismrag.organization(id) ON DELETE CASCADE,
    external_id     VARCHAR(255) NOT NULL,
    display_name    VARCHAR(200) NOT NULL,
    tenant_id       UUID REFERENCES prismrag.tenant(id) ON DELETE SET NULL,
    default_role    VARCHAR(20) NOT NULL DEFAULT 'member',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, external_id)
);

CREATE TABLE IF NOT EXISTS prismrag.scim_group_member (
    group_id    UUID NOT NULL REFERENCES prismrag.scim_group(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES prismrag.user_account(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

-- ── MFA pending login challenges ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.mfa_challenge (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES prismrag.user_account(id) ON DELETE CASCADE,
    challenge_hash  TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mfa_challenge_exp ON prismrag.mfa_challenge (expires_at);

-- ── Password reset tokens ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.password_reset_token (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES prismrag.user_account(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_password_reset_user ON prismrag.password_reset_token (user_id);

-- ── Transactional email log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.email_log (
    id              BIGSERIAL PRIMARY KEY,
    to_address      VARCHAR(320) NOT NULL,
    subject         VARCHAR(500) NOT NULL,
    template        VARCHAR(80)  NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'queued',
    provider        VARCHAR(30)  NOT NULL DEFAULT 'azure_acs',
    provider_msg_id TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_email_log_to ON prismrag.email_log (to_address, created_at DESC);

-- ── SLA incident log (status page) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.status_incident (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(300) NOT NULL,
    status          VARCHAR(30)  NOT NULL DEFAULT 'investigating',
    impact          VARCHAR(30)  NOT NULL DEFAULT 'minor',
    component       VARCHAR(50)  NOT NULL DEFAULT 'api',
    message         TEXT NOT NULL DEFAULT '',
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
