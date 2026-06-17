-- PrismRAG quality logging tables
-- Run once after schema.sql

CREATE TABLE IF NOT EXISTS prismrag.quality_search_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    user_id         UUID,
    mapping_id      UUID,
    query           TEXT        NOT NULL,
    top_k           INT         NOT NULL DEFAULT 5,
    result_count    INT         NOT NULL DEFAULT 0,
    top_category    TEXT,
    category_filter TEXT,
    score_spread    NUMERIC(6,4) NOT NULL DEFAULT 0,
    mean_score      NUMERIC(6,4) NOT NULL DEFAULT 0,
    latency_ms      INT         NOT NULL DEFAULT 0,
    results_sample  JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_qsl_tenant_created
    ON prismrag.quality_search_log (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_qsl_user_created
    ON prismrag.quality_search_log (user_id, created_at DESC);


CREATE TABLE IF NOT EXISTS prismrag.quality_deliberation_log (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id                  UUID        NOT NULL,
    user_id                     UUID,
    tenant_id                   UUID,
    question                    TEXT        NOT NULL,
    domain_count_requested      INT         NOT NULL DEFAULT 7,
    domains_discovered          INT         NOT NULL DEFAULT 0,
    domain_sources              JSONB,
    vertical_mean_confidence    NUMERIC(5,3),
    synthesis_confidence        NUMERIC(5,3),
    completeness_score          NUMERIC(4,3),
    conflict_detected           BOOLEAN     NOT NULL DEFAULT FALSE,
    total_latency_ms            INT         NOT NULL DEFAULT 0,
    phase_latencies_ms          JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_qdl_user_created
    ON prismrag.quality_deliberation_log (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_qdl_session
    ON prismrag.quality_deliberation_log (session_id);
