-- PrismRAG — Enterprise extensions (RBAC, OIDC, job queue, plan columns)
-- Run after auth_schema.sql

-- ── Extended plan quotas (single source of truth) ─────────────────────────────
ALTER TABLE prismrag.plan_quota ADD COLUMN IF NOT EXISTS monthly_searches INT NOT NULL DEFAULT 500;
ALTER TABLE prismrag.plan_quota ADD COLUMN IF NOT EXISTS req_per_min INT NOT NULL DEFAULT 20;
ALTER TABLE prismrag.plan_quota ADD COLUMN IF NOT EXISTS max_file_bytes BIGINT NOT NULL DEFAULT 10485760;
ALTER TABLE prismrag.plan_quota ADD COLUMN IF NOT EXISTS log_retention_days INT NOT NULL DEFAULT 7;
ALTER TABLE prismrag.plan_quota ADD COLUMN IF NOT EXISTS mlp_train BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE prismrag.plan_quota SET
    monthly_searches = 500, req_per_min = 20, max_file_bytes = 10485760,
    log_retention_days = 7, mlp_train = FALSE
WHERE plan = 'free';

UPDATE prismrag.plan_quota SET
    monthly_chunks = 50000, monthly_searches = 20000, req_per_min = 120,
    max_tenants = 3, max_file_bytes = 104857600, log_retention_days = 30,
    graph_rag = TRUE, mlp_train = FALSE
WHERE plan = 'starter';

UPDATE prismrag.plan_quota SET
    monthly_chunks = 500000, monthly_searches = 150000, req_per_min = 600,
    max_tenants = 20, max_file_bytes = 524288000, log_retention_days = 30,
    tier2_mlp = TRUE, graph_rag = TRUE, bridge_vectors = TRUE, mlp_train = TRUE
WHERE plan = 'professional';

UPDATE prismrag.plan_quota SET
    monthly_chunks = 0, monthly_searches = 0, req_per_min = 0,
    max_tenants = -1, max_file_bytes = 0, log_retention_days = 90,
    tier2_mlp = TRUE, graph_rag = TRUE, bridge_vectors = TRUE, mlp_train = TRUE
WHERE plan = 'enterprise';

-- ── Workspace membership (RBAC) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.tenant_member (
    tenant_id   UUID NOT NULL REFERENCES prismrag.tenant(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES prismrag.user_account(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL DEFAULT 'member',
    invited_by  UUID REFERENCES prismrag.user_account(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id),
    CONSTRAINT ck_tenant_member_role CHECK (role IN ('owner', 'admin', 'member', 'viewer'))
);
CREATE INDEX IF NOT EXISTS ix_tenant_member_user ON prismrag.tenant_member (user_id);

-- Backfill owners from legacy owner_email
INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
SELECT t.id, u.id, 'owner'
FROM prismrag.tenant t
JOIN prismrag.user_account u ON lower(u.email) = lower(t.owner_email)
WHERE t.owner_email IS NOT NULL AND t.owner_email <> ''
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- ── OIDC / SSO identities ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.oidc_identity (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES prismrag.user_account(id) ON DELETE CASCADE,
    provider        VARCHAR(50) NOT NULL DEFAULT 'default',
    subject         VARCHAR(255) NOT NULL,
    email           VARCHAR(320) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, subject)
);
CREATE INDEX IF NOT EXISTS ix_oidc_user ON prismrag.oidc_identity (user_id);

-- Allow SSO-only accounts (empty password)
ALTER TABLE prismrag.user_account ALTER COLUMN password_hash DROP NOT NULL;

-- ── API key scopes ────────────────────────────────────────────────────────────
ALTER TABLE prismrag.api_key ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{read,write}';

-- ── Async job queue (Postgres worker) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prismrag.job_queue (
    id              BIGSERIAL PRIMARY KEY,
    job_id          UUID NOT NULL,
    tenant_id       UUID NOT NULL,
    payload         JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    worker_id       TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_job_queue_pending ON prismrag.job_queue (status, created_at)
    WHERE status = 'pending';

-- ── Audit log trace id ────────────────────────────────────────────────────────
ALTER TABLE prismrag.api_request_log ADD COLUMN IF NOT EXISTS trace_id VARCHAR(64) NOT NULL DEFAULT '';
