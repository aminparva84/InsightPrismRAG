-- Healthcare QA Domain Seed Data
-- Tenant: qa-healthcare | Mapping: clinical-standard
-- Prerequisites: schema.sql, auth_schema.sql, enterprise_schema.sql, qa_user_seed.sql

BEGIN;

-- ── QA Tenant ─────────────────────────────────────────────────────────────────
INSERT INTO prismrag.tenant (id, name, owner_email, tier)
VALUES (
    '10000000-0000-0000-0000-000000000001',
    'QA Healthcare Clinic',
    'qa-local@test.prismrag.io',
    'tier1'
) ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    owner_email = EXCLUDED.owner_email;

-- Link QA user as owner
INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
VALUES ('10000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'owner')
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- ── Mapping version ───────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_version (id, tenant_id, version, strategy, config_json, status)
VALUES (
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    1,
    'rules',
    '{"name": "clinical-standard"}',
    'active'
) ON CONFLICT (id) DO UPDATE SET status = 'active';

-- ── Categories ────────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_category (mapping_id, category_slug, category_label, sort_order) VALUES
('30000000-0000-0000-0000-000000000001', 'diagnosis',       'Diagnosis & Classification',    1),
('30000000-0000-0000-0000-000000000001', 'symptoms',        'Symptoms & Clinical Signs',     2),
('30000000-0000-0000-0000-000000000001', 'treatment',       'Treatment & Therapy',           3),
('30000000-0000-0000-0000-000000000001', 'medication',      'Medication & Pharmacotherapy',  4),
('30000000-0000-0000-0000-000000000001', 'procedures',      'Clinical Procedures',           5),
('30000000-0000-0000-0000-000000000001', 'lab_results',     'Laboratory Results',            6),
('30000000-0000-0000-0000-000000000001', 'patient_safety',  'Patient Safety & Risk',         7)
ON CONFLICT (mapping_id, category_slug) DO NOTHING;

-- ── Mapping Rules ─────────────────────────────────────────────────────────────
INSERT INTO prismrag.mapping_rule (mapping_id, word, category_slug, weight, source) VALUES
-- Diagnosis
('30000000-0000-0000-0000-000000000001', 'hypertension',           'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'diabetes',               'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'pneumonia',              'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'sepsis',                 'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'myocardial_infarction',  'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'stroke',                 'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'asthma',                 'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'copd',                   'diagnosis',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'atrial_fibrillation',    'diagnosis',      1.0, 'manual'),
-- Symptoms
('30000000-0000-0000-0000-000000000001', 'dyspnea',                'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'chest_pain',             'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'tachycardia',            'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'hypotension',            'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'fever',                  'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'altered_consciousness',  'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'cyanosis',               'symptoms',       1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'edema',                  'symptoms',       1.0, 'manual'),
-- Treatment
('30000000-0000-0000-0000-000000000001', 'antibiotics',            'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'dialysis',               'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'oxygen_therapy',         'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'mechanical_ventilation', 'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'physiotherapy',          'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'chemotherapy',           'treatment',      1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'radiotherapy',           'treatment',      1.0, 'manual'),
-- Medication
('30000000-0000-0000-0000-000000000001', 'metformin',              'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'lisinopril',             'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'warfarin',               'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'heparin',                'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'amoxicillin',            'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'insulin',                'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'aspirin',                'medication',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'statins',                'medication',     1.0, 'manual'),
-- Procedures
('30000000-0000-0000-0000-000000000001', 'ecg',                    'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'echocardiogram',         'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'biopsy',                 'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'endoscopy',              'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'ct_scan',                'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'mri',                    'procedures',     1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'coronary_angiography',   'procedures',     1.0, 'manual'),
-- Lab Results
('30000000-0000-0000-0000-000000000001', 'hba1c',                  'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'creatinine',             'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'troponin',               'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'white_blood_cell',       'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'hemoglobin',             'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'inr',                    'lab_results',    1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'blood_glucose',          'lab_results',    1.0, 'manual'),
-- Patient Safety
('30000000-0000-0000-0000-000000000001', 'drug_allergy',                 'patient_safety', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'fall_risk',                    'patient_safety', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'pressure_ulcer',               'patient_safety', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'medication_error',             'patient_safety', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'hospital_acquired_infection',  'patient_safety', 1.0, 'manual'),
('30000000-0000-0000-0000-000000000001', 'anaphylaxis',                  'patient_safety', 1.0, 'manual')
ON CONFLICT (mapping_id, word) DO NOTHING;

COMMIT;
