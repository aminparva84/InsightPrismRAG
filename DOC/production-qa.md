# Production QA — PrismRAG API

End-to-end validation of the published API at **https://prismrag.insightits.com** using three seeded domains (healthcare, pharmacy, finance), simulated user table data, and a dedicated Azure Postgres test account.

## What gets seeded

| Asset | ID / value |
|-------|------------|
| QA user | `qa-prod@insightits.com` / `QaProdPass!2026#` |
| User UUID | `20000000-0000-0000-0000-000000000010` |
| Healthcare tenant | `10000000-0000-0000-0000-000000000001` — QA Healthcare Clinic |
| Pharmacy tenant | `10000000-0000-0000-0000-000000000002` — QA PharmaCo |
| Finance tenant | `10000000-0000-0000-0000-000000000003` — QA FinanceCo |
| Mapping versions | `30000000-...-0001`, `...-0002`, `...-0003` (rules-based categories) |

### Simulated “table” data (ingested via API)

Rich inline records live in `tests/fixtures/production_sample_records.json`:

| Domain | Simulated table | Sample content |
|--------|-----------------|----------------|
| Healthcare | `clinical_notes` | Diabetes, hypertension, troponin, drug allergy notes |
| Pharmacy | `drug_monographs` | CYP450 interactions, insulin storage, renal dosing |
| Finance | `analyst_reports` | DCF/WACC, VaR, free cash flow, SEC filing excerpts |

SQL fixtures (`tests/fixtures/*_seed.sql`) plant tenants, mapping categories, and word→category rules. Ingest jobs push the sample records through the worker (embeddings + graph).

## Prerequisites

1. **Azure Postgres** — `prismrag` database on `psql-insight-hospital-prod` with schema applied.
2. **Container App worker** — `prismrag-worker` must process Service Bus jobs (Gemini API key configured).
3. **Local Python** — `pip install -r requirements.txt` (includes `pytest`, `requests`, `psycopg2-binary`).

## One-command run

```powershell
# Set Azure DSN (not your local Docker DSN)
$env:PRISMRAG_AZURE_DB_DSN = "<postgresql://user:pass@psql-insight-hospital-prod.postgres.database.azure.com:5432/prismrag?sslmode=require>"

.\scripts\run_production_qa.ps1
```

Reports are written to `DOC/qa-reports/production-qa-<timestamp>.txt`.

### First-time Azure setup (already done once on 2026-06-18)

1. `python scripts/init_azure_schema.py --dsn $env:PRISMRAG_AZURE_DB_DSN`
2. `python tests/seed_qa_data.py --production --drop --dsn $env:PRISMRAG_AZURE_DB_DSN`
3. Ensure Container App `db-dsn` secret uses the **`prismrag`** database (not `insight_hospital`)
4. Deploy API with ingest UUID fix (`prismrag/pipeline/job.py`) via `[publish]` commit

See `DOC/qa-reports/production-qa-2026-06-18.txt` for the latest run results.

### Fetch DSN from Key Vault

```powershell
az keyvault secret show `
  --vault-name kvinsightitsprod01 `
  --name database-url `
  -o tsv --query value
```

Use the connection string but change the database name to `prismrag` if the secret points at a different DB.

## Manual steps

### 1. Seed Azure database

```powershell
python tests/seed_qa_data.py --production --drop --dsn $env:PRISMRAG_AZURE_DB_DSN
```

### 2. Verify auth

```powershell
python scripts/qa_setup_prod_user.py --url https://prismrag.insightits.com
```

### 3. Run tests

```powershell
$env:PRISMRAG_TEST_URL      = "https://prismrag.insightits.com"
$env:PRISMRAG_TEST_EMAIL    = "qa-prod@insightits.com"
$env:PRISMRAG_TEST_PASSWORD = "QaProdPass!2026#"
$env:QA_SEEDED              = "1"

pytest tests/test_production_api.py tests/test_smoke.py -v --tb=short
```

Or use the existing wrapper:

```powershell
.\qa_run.ps1 -Target prod
```

## Test coverage

| Suite | File | What it checks |
|-------|------|----------------|
| Production smoke | `tests/test_production_api.py::TestProductionSmoke` | Health, login, seeded tenants visible |
| Mappings | `TestProductionMappings` | Mapping metadata per domain |
| Ingest | `TestProductionIngest` | Inline jobs complete per domain (worker + Gemini) |
| Search | `TestProductionSearch` | Graph RAG returns category-relevant hits |
| Deploy smoke | `tests/test_smoke.py` | Auth, tenants, billing, status (no seed required) |

## Expected results

When the worker and Gemini are healthy:

- Health: `200` with `status: ok`
- Login: JWT returned for `qa-prod@insightits.com`, plan `professional`
- Tenants: all three seeded tenant UUIDs appear in `GET /api/v1/prismrag/tenants`
- Ingest: each domain job reaches `completed` with `records_written >= 8`
- Search: queries return results tagged with expected categories (e.g. `medication`, `valuation`)

### Common failures

| Symptom | Likely cause |
|---------|--------------|
| Login 401 | DB not seeded with `--production`, or wrong password |
| Job stays `queued` | Worker scaled to zero — submit a job and wait for scale-up, or check Service Bus |
| Job `failed` | Missing/invalid `GEMINI_API_KEY` on worker |
| Search empty | Ingest did not complete; check job status |
| Tenant missing | Re-run `seed_qa_data.py --production --drop` |

## Security notes

- The QA password is documented for automation only. Rotate by updating `tests/fixtures/qa_production_user_seed.sql` and re-seeding.
- Do **not** commit `PRISMRAG_AZURE_DB_DSN` to git.
- The QA user has `professional` plan in DB — it does not create a live Stripe subscription.

## Files reference

| Path | Purpose |
|------|---------|
| `tests/seed_qa_data.py` | Seed script (`--production` flag) |
| `tests/fixtures/qa_production_user_seed.sql` | Production QA user |
| `tests/fixtures/*_seed.sql` | Domain tenants + mappings |
| `tests/fixtures/production_sample_records.json` | Simulated table rows |
| `tests/test_production_api.py` | Production E2E tests |
| `scripts/run_production_qa.ps1` | Orchestration + report |
| `scripts/qa_setup_prod_user.py` | Auth verification |
| `qa_run.ps1` | Local + prod test runner |
