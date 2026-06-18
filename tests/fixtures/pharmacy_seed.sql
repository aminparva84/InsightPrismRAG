-- Pharmacy QA Domain Seed Data
-- Tenant: qa-pharmacy | Mapping: pharma-standard
-- Prerequisites: schema.sql, auth_schema.sql, enterprise_schema.sql, qa_user_seed.sql

BEGIN;

-- ── QA Tenant ─────────────────────────────────────────────────────────────────
INSERT INTO prismrag.tenant (id, name, owner_email, tier)
VALUES (
    '10000000-0000-0000-0000-000000000002',
    'QA PharmaCo',
    'qa-local@test.prismrag.io',
    'tier1'
) ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    owner_email = EXCLUDED.owner_email;

-- Link QA user as owner
INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
VALUES ('10000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'owner')
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- ── Mapping version ───────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_version (id, tenant_id, version, strategy, config_json, status)
VALUES (
    '30000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    1,
    'rules',
    '{"name": "pharma-standard"}',
    'active'
) ON CONFLICT (id) DO UPDATE SET status = 'active';

-- ── Categories ────────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_category (mapping_id, category_slug, category_label, sort_order) VALUES
('30000000-0000-0000-0000-000000000002', 'drug_interactions',   'Drug Interactions',             1),
('30000000-0000-0000-0000-000000000002', 'dosage',              'Dosing & Administration',       2),
('30000000-0000-0000-0000-000000000002', 'contraindications',   'Contraindications & Warnings',  3),
('30000000-0000-0000-0000-000000000002', 'adverse_effects',     'Adverse Effects & Toxicity',    4),
('30000000-0000-0000-0000-000000000002', 'pharmacokinetics',    'Pharmacokinetics & Metabolism', 5),
('30000000-0000-0000-0000-000000000002', 'mechanisms',          'Mechanism of Action',           6),
('30000000-0000-0000-0000-000000000002', 'storage',             'Storage & Stability',           7)
ON CONFLICT (mapping_id, category_slug) DO NOTHING;

-- ── Mapping Rules ─────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_rule (mapping_id, word, category_slug, weight, source) VALUES
-- Drug Interactions
('30000000-0000-0000-0000-000000000002', 'cyp450',                  'drug_interactions', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'cyp3a4',                  'drug_interactions', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'inhibitor',               'drug_interactions', 0.8, 'manual'),
('30000000-0000-0000-0000-000000000002', 'inducer',                 'drug_interactions', 0.8, 'manual'),
('30000000-0000-0000-0000-000000000002', 'warfarin_interaction',    'drug_interactions', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'polypharmacy',            'drug_interactions', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'drug_drug_interaction',   'drug_interactions', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'synergistic',             'drug_interactions', 0.9, 'manual'),
-- Dosage
('30000000-0000-0000-0000-000000000002', 'loading_dose',            'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'maintenance_dose',        'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'maximum_daily_dose',      'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'pediatric_dose',          'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'renal_dose_adjustment',   'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'hepatic_dose_adjustment', 'dosage',            1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'titration',               'dosage',            0.9, 'manual'),
-- Contraindications
('30000000-0000-0000-0000-000000000002', 'contraindicated',          'contraindications', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'pregnancy_category',       'contraindications', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'renal_failure',            'contraindications', 0.9, 'manual'),
('30000000-0000-0000-0000-000000000002', 'hepatic_impairment',       'contraindications', 0.9, 'manual'),
('30000000-0000-0000-0000-000000000002', 'black_box_warning',        'contraindications', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'absolute_contraindication','contraindications', 1.0, 'manual'),
-- Adverse Effects
('30000000-0000-0000-0000-000000000002', 'hepatotoxicity',          'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'nephrotoxicity',          'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'qt_prolongation',         'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'agranulocytosis',         'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'anaphylaxis',             'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'serotonin_syndrome',      'adverse_effects',   1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'adverse_drug_reaction',   'adverse_effects',   1.0, 'manual'),
-- Pharmacokinetics
('30000000-0000-0000-0000-000000000002', 'half_life',                'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'bioavailability',          'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'volume_of_distribution',   'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'protein_binding',          'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'clearance',                'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'first_pass_metabolism',    'pharmacokinetics',  1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'peak_plasma_concentration','pharmacokinetics',  1.0, 'manual'),
-- Mechanisms
('30000000-0000-0000-0000-000000000002', 'receptor_agonist',        'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'receptor_antagonist',     'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'enzyme_inhibition',       'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'ion_channel_blockade',    'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'beta_blocker',            'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'ace_inhibitor',           'mechanisms',        1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'ssri',                    'mechanisms',        1.0, 'manual'),
-- Storage
('30000000-0000-0000-0000-000000000002', 'refrigerate',             'storage',           1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'light_sensitive',         'storage',           1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'expiry_date',             'storage',           1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'cold_chain',              'storage',           1.0, 'manual'),
('30000000-0000-0000-0000-000000000002', 'room_temperature',        'storage',           1.0, 'manual')
ON CONFLICT (mapping_id, word) DO NOTHING;

COMMIT;
