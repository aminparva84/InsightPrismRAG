# PrismRAG — Agent Handoff & Deployment Status

> Last updated: 2026-06-17  
> Branch: `main`  
> GitHub repo: https://github.com/aminparva84/InsightPrismRAG  
> Owner: aminparva84 / insightits.info@gmail.com

---

## Current Deployment Status

| Component | Status | Detail |
|---|---|---|
| Azure resource group | ✅ Created | `prismrag-rg` in `eastus2` |
| Azure Container Registry | ✅ Created | `prismragacr.azurecr.io` |
| Service principal | ✅ Created | `prismrag-deploy` (Contributor on RG) |
| GitHub Actions pipeline | ✅ Written | `.github/workflows/deploy.yml` |
| GitHub secrets | ✅ Partial (5/10 set) | See table below |
| Code committed to GitHub | ⏳ Pending user approval | `git commit` requires explicit "yes commit" from user |
| Docker images in ACR | ❌ Not yet | Blocked on commit + pipeline run |
| Container Apps deployed | ❌ Not yet | Blocked on images |
| Live API URL | ❌ Not yet | Will appear after first successful pipeline run |

**The live URL will be:** `https://prismrag-api.<hash>.eastus2.azurecontainerapps.io`  
After deployment, run: `az containerapp show --name prismrag-api --resource-group prismrag-rg --query properties.configuration.ingress.fqdn -o tsv`

The user will also set up a custom subdomain: `api.prismrag.insightits.com` → DNS CNAME to the above URL, after QA passes.

---

## GitHub Secrets Status

Set in `aminparva84/InsightPrismRAG` → Settings → Secrets → Actions:

| Secret | Status | Notes |
|---|---|---|
| `AZURE_CREDENTIALS` | ✅ Set | Service principal JSON for `az login` |
| `ACR_USERNAME` | ✅ Set | `prismragacr` (ACR admin) |
| `ACR_PASSWORD` | ✅ Set | ACR admin password |
| `GEMINI_API_KEY` | ✅ Set | From `.env` |
| `JWT_SECRET` | ✅ Set | 64-char hex from `.env` |
| `DB_CONNECTION_STRING` | ❌ Missing | Needs Neon/Supabase DSN for Phase 1 production DB |
| `SERVICE_BUS_CONN_STR` | ❌ Missing | Run `az servicebus namespace authorization-rule keys list --resource-group prismrag-rg --namespace-name prismrag-bus --name RootManageSharedAccessKey --query primaryConnectionString -o tsv` after deploy.sh creates the namespace |
| `STRIPE_SECRET_KEY` | ❌ Missing | From Stripe dashboard |
| `STRIPE_PRICE_STARTER` | ❌ Missing | From Stripe dashboard |
| `STRIPE_PRICE_PROF` | ❌ Missing | From Stripe dashboard |
| `STRIPE_PRICE_ENTERPRISE` | ❌ Missing | From Stripe dashboard |
| `STRIPE_WEBHOOK_SECRET` | ❌ Missing | From Stripe dashboard |

---

## Azure Infrastructure

```
Subscription: d7a6d032-e961-4519-bd35-b7e989403c05  (Azure subscription 1)
Resource Group: prismrag-rg  (eastus2)
ACR: prismragacr  →  prismragacr.azurecr.io
Service Bus: prismrag-bus  (created by deploy.sh / pipeline)
Key Vault: prismrag-kv  (created by deploy.sh)
Container App - API: prismrag-api  (minReplicas=1, port 8001)
Container App - Worker: prismrag-worker  (scale-to-zero, Service Bus trigger)
Log Analytics: prismrag-logs
```

**Phase flags** (set in GitHub Actions `deploy` step):
- Phase 1 (current): `externalDb=true, deployRedis=false` — uses external Neon DB, ~$12-31/mo
- Phase 2: `externalDb=false` — Azure Postgres Flexible B2s, ~$80-130/mo
- Phase 3: `deployRedis=true` — add Redis, ~$250+/mo

**After first deploy, enable pgvector:**
```bash
az postgres flexible-server parameter set \
  --resource-group prismrag-rg \
  --server-name prismrag-pg \
  --name azure.extensions \
  --value vector
```

---

## CI/CD Pipeline

File: `.github/workflows/deploy.yml`

**Triggers:** push to `main`, or manual `workflow_dispatch` (with optional `image_tag` input)

**Jobs:**
1. `lint` — Python 3.11, installs deps, checks `main.py` imports (no DB needed)
2. `build` — builds `prismrag-api` and `prismrag-worker` (`linux/amd64`), pushes to ACR with SHA tag + `latest`. Uses GitHub Actions layer cache.
3. `deploy` — `azure/login` with `AZURE_CREDENTIALS`, `azure/arm-deploy` with Bicep, polls `/api/v1/prismrag/health` 20× (300s timeout)

**Docker images:**
- `prismragacr.azurecr.io/prismrag-api:<sha>` — built from `Dockerfile`
- `prismragacr.azurecr.io/prismrag-worker:<sha>` — built from `Dockerfile.worker`

---

## API Architecture

**Base URL (local):** `http://localhost:8001`  
**Base URL (production):** `https://prismrag-api.<hash>.eastus2.azurecontainerapps.io`

**Versioned API routes** (all under `/api/v1/`):
- `/api/v1/prismrag/*` — RAG engine (search, jobs, bridge, communities, quality)
- `/api/v1/auth/*` — JWT auth, API keys, MFA, OIDC
- `/api/v1/billing/*` — Stripe subscription management
- `/api/v1/deliberation/*` — HVS deliberation engine
- `/api/v1/tenant/*` — Multi-tenant management
- `/api/v1/scim/*` — SCIM 2.0 user provisioning (enterprise)
- `/api/v1/status/*` — System status page API
- `/metrics` — Prometheus-format metrics
- `/docs` — Swagger UI
- Legacy shim: `/api/<path>` → `/api/v1/<path>` (via `LegacyApiMiddleware`)

**Middleware stack** (outer → inner):
1. `CORSMiddleware`
2. `AuditMiddleware`
3. `MetricsMiddleware`
4. `RequestIdMiddleware`
5. `LegacyApiMiddleware`

---

## Products

### 1. PrismRAG (Semantic Re-mapping Engine)
- ML strategies: **Tier 1 (RulesStrategy)** — deterministic 768→256-d projection; **Tier 2 (MLPStrategy)** — PyTorch MLP with InfoNCE loss
- Embeddings: Gemini `text-embedding-004` (768-d), cached in DB
- Graph: Louvain community detection + LLM labelling, HNSW pgvector search

### 2. Deliberation (HVS Pipeline)
- Horizontal → Vertical (parallel expert queries) → Synthesis
- Cost: ~$0.003 COGS/deliberation (Gemini 2.0 Flash)
- Overage: $0.25/deliberation above plan limit
- Pricing doc: `DOC/deliberation.md`

---

## QA Test Suite

**Seed data:** `tests/fixtures/healthcare_seed.sql`, `pharmacy_seed.sql`, `finance_seed.sql`  
**Test runner:**
```bash
# Seed QA data into DB
python tests/seed_qa_data.py --domain all

# Run all tests against local server
BASE_URL=http://localhost:8001 pytest tests/ -v

# Run against production after deploy
BASE_URL=https://api.prismrag.insightits.com pytest tests/ -v
```

**Quality reports:** `tests/quality_report.json` (generated by `test_quality.py`)  
**Quality summary APIs:**
- `GET /api/v1/prismrag/quality/search?tenant_id=<id>&days=7`
- `GET /api/v1/prismrag/quality/deliberation?days=7`

---

## Pending Actions (for next agent or human)

1. **Commit the code** — user must say "yes commit" explicitly. Files ready:
   - `.github/workflows/deploy.yml`, `Makefile`, `pytest.ini`, `requirements-test.txt`
   - `prismrag/quality/` (3 files), `tests/` (8 files + 3 SQL fixtures)
   - Modified: `deliberation_routes.py`, `routes.py`, `db.py`, `container-apps.bicep`, `deploy.sh`, `.env.example`, `web/index.html`, `DOC/deliberation.md`

2. **Add missing GitHub secrets** (see table above) — especially `DB_CONNECTION_STRING` for the Neon production DSN.

3. **Set up Neon DB** — create a free project at neon.tech, copy the DSN, add as `DB_CONNECTION_STRING` GitHub secret.

4. **Push to `main`** — triggers the GitHub Actions pipeline automatically.

5. **Post-deploy DNS** — user will CNAME `api.prismrag.insightits.com` → Azure Container App FQDN.

6. **Run QA** — `BASE_URL=https://api.prismrag.insightits.com pytest tests/ -v` after DNS propagates.

7. **Wire quality logging** — `prismrag/quality/metrics.py` has `log_search()` and `log_deliberation()` but they are not yet called from the search/deliberation endpoints. Call them after the relevant `retrieve()` / synthesis calls.
