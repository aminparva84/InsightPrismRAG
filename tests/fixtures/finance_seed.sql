-- Finance QA Domain Seed Data
-- Tenant: qa-finance | Mapping: finance-standard
-- Prerequisites: schema.sql, auth_schema.sql, enterprise_schema.sql, qa_user_seed.sql

BEGIN;

-- ── QA Tenant ─────────────────────────────────────────────────────────────────
INSERT INTO prismrag.tenant (id, name, owner_email, tier)
VALUES (
    '10000000-0000-0000-0000-000000000003',
    'QA FinanceCo',
    'qa-local@test.prismrag.io',
    'tier1'
) ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    owner_email = EXCLUDED.owner_email;

-- Link QA user as owner
INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
VALUES ('10000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'owner')
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- ── Mapping version ───────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_version (id, tenant_id, version, strategy, config_json, status)
VALUES (
    '30000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000003',
    1,
    'rules',
    '{"name": "finance-standard"}',
    'active'
) ON CONFLICT (id) DO UPDATE SET status = 'active';

-- ── Categories ────────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_category (mapping_id, category_slug, category_label, sort_order) VALUES
('30000000-0000-0000-0000-000000000003', 'risk',             'Risk & Compliance',         1),
('30000000-0000-0000-0000-000000000003', 'growth',           'Growth & Opportunity',      2),
('30000000-0000-0000-0000-000000000003', 'valuation',        'Valuation & Pricing',       3),
('30000000-0000-0000-0000-000000000003', 'liquidity',        'Liquidity & Cash Flow',     4),
('30000000-0000-0000-0000-000000000003', 'debt',             'Debt & Capital Structure',  5),
('30000000-0000-0000-0000-000000000003', 'market_analysis',  'Market Analysis',           6),
('30000000-0000-0000-0000-000000000003', 'regulatory',       'Regulatory & Reporting',    7)
ON CONFLICT (mapping_id, category_slug) DO NOTHING;

-- ── Mapping Rules ─────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_rule (mapping_id, word, category_slug, weight, source) VALUES
-- Risk
('30000000-0000-0000-0000-000000000003', 'volatility',            'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'var',                   'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'credit_risk',           'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'market_risk',           'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'operational_risk',      'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'beta',                  'risk',            0.9, 'manual'),
('30000000-0000-0000-0000-000000000003', 'drawdown',              'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'stress_test',           'risk',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'default_probability',   'risk',            1.0, 'manual'),
-- Growth
('30000000-0000-0000-0000-000000000003', 'alpha',                 'growth',          1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'revenue_growth',        'growth',          1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'ebitda_growth',         'growth',          1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'market_share',          'growth',          1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'cagr',                  'growth',          1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'expansion',             'growth',          0.8, 'manual'),
('30000000-0000-0000-0000-000000000003', 'acquisition_target',    'growth',          0.9, 'manual'),
-- Valuation
('30000000-0000-0000-0000-000000000003', 'dcf',                   'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'ebitda',                'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'pe_ratio',              'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'ev_ebitda',             'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'wacc',                  'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'terminal_value',        'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'fair_value',            'valuation',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'book_value',            'valuation',       0.9, 'manual'),
-- Liquidity
('30000000-0000-0000-0000-000000000003', 'current_ratio',         'liquidity',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'quick_ratio',           'liquidity',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'free_cash_flow',        'liquidity',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'operating_cash_flow',   'liquidity',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'working_capital',       'liquidity',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'burn_rate',             'liquidity',       1.0, 'manual'),
-- Debt
('30000000-0000-0000-0000-000000000003', 'leverage_ratio',        'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'debt_to_equity',        'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'interest_coverage',     'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'covenant',              'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'bond_yield',            'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'credit_rating',         'debt',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'refinancing',           'debt',            0.9, 'manual'),
-- Market Analysis
('30000000-0000-0000-0000-000000000003', 'total_addressable_market','market_analysis',1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'competitive_moat',       'market_analysis',1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'pricing_power',          'market_analysis',1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'industry_cycle',         'market_analysis',1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'market_concentration',   'market_analysis',1.0, 'manual'),
-- Regulatory
('30000000-0000-0000-0000-000000000003', 'sec_filing',             'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'ifrs',                   'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'gaap',                   'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'aml',                    'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'kyc',                    'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'sox_compliance',         'regulatory',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000003', 'capital_adequacy',       'regulatory',     1.0, 'manual')
ON CONFLICT (mapping_id, word) DO NOTHING;

COMMIT;
