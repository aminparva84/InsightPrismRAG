-- Production QA user (Azure Postgres — prismrag.insightits.com)
-- Email:    qa-prod@insightits.com
-- Password: QaProdPass!2026#
-- password_hash generated via prismrag.auth.auth.hash_password

BEGIN;

INSERT INTO prismrag.user_account (
    id,
    email,
    password_hash,
    full_name,
    company,
    plan,
    email_verified,
    is_active,
    subscription_status
) VALUES (
    '20000000-0000-0000-0000-000000000010',
    'qa-prod@insightits.com',
    '$2b$12$5ujfNNN224cHcuoizB1Qcue570qlSzAxwmbe9XIaXltDcvBI.XK.K',
    'PrismRAG Production QA',
    'Insight IT Solutions',
    'professional',
    TRUE,
    TRUE,
    'active'
) ON CONFLICT (email) DO UPDATE SET
    id                  = EXCLUDED.id,
    password_hash       = EXCLUDED.password_hash,
    full_name           = EXCLUDED.full_name,
    plan                = EXCLUDED.plan,
    email_verified      = EXCLUDED.email_verified,
    is_active           = EXCLUDED.is_active,
    subscription_status = EXCLUDED.subscription_status;

COMMIT;
