-- QA shared user account (referenced by all 3 domain seed files)
-- password_hash is bcrypt of "QaTestPass!123"
-- Run BEFORE healthcare_seed.sql / pharmacy_seed.sql / finance_seed.sql

BEGIN;

INSERT INTO prismrag.user_account (
    id,
    email,
    password_hash,
    full_name,
    company,
    plan,
    email_verified,
    is_active
) VALUES (
    '20000000-0000-0000-0000-000000000001',
    'qa-local@test.prismrag.io',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMqJqhCanUIa17E9xQBjYBlzoe',
    'QA Local User',
    'PrismRAG QA',
    'professional',
    TRUE,
    TRUE
) ON CONFLICT (id) DO UPDATE SET
    email           = EXCLUDED.email,
    plan            = EXCLUDED.plan,
    email_verified  = EXCLUDED.email_verified,
    is_active       = EXCLUDED.is_active;

-- Also upsert by email in case the UUID row was already created by the API
INSERT INTO prismrag.user_account (
    id,
    email,
    password_hash,
    full_name,
    company,
    plan,
    email_verified,
    is_active
) VALUES (
    '20000000-0000-0000-0000-000000000001',
    'qa-local@test.prismrag.io',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMqJqhCanUIa17E9xQBjYBlzoe',
    'QA Local User',
    'PrismRAG QA',
    'professional',
    TRUE,
    TRUE
) ON CONFLICT (email) DO UPDATE SET
    plan           = EXCLUDED.plan,
    email_verified = EXCLUDED.email_verified,
    is_active      = EXCLUDED.is_active;

COMMIT;
