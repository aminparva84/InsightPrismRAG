-- Migration: IP Allowlist support
-- Run after enterprise_features_schema.sql

ALTER TABLE prismrag.organization
    ADD COLUMN IF NOT EXISTS ip_allowlist_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS prismrag.ip_allowlist (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES prismrag.organization(id) ON DELETE CASCADE,
    cidr            VARCHAR(50) NOT NULL,
    label           VARCHAR(200) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, cidr)
);

CREATE INDEX IF NOT EXISTS ix_ip_allowlist_org ON prismrag.ip_allowlist (organization_id);
