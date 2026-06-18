# PrismRAG — Architecture Document

**Version:** 0.1  
**Date:** 2026-06-18

---

## 1. System Map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PUBLIC INTERNET                                     │
│                                                                              │
│  Browser  ──────────────►  prismrag.insightits.com  (nginx → container)     │
│  pip user ──────────────►  PyPI  →  prismrag-patch library                  │
│  API client ────────────►  prismrag.insightits.com/api/v1/...               │
└──────────────────────────────────────────────────────────────────────────────┘
            │                            │
            ▼                            ▼
┌─────────────────────┐       ┌───────────────────────────────────────┐
│  Azure Container    │       │  prismrag-patch (pip library)          │
│  Apps               │       │                                        │
│  ┌───────────────┐  │       │  PrismRAGPatch                        │
│  │  prismrag-api │  │       │  └── validate_license() ──────────────┼──► /api/v1/lib/validate
│  │  (FastAPI)    │◄─┼───────│  └── remap_vector()                   │
│  └───────────────┘  │       │                                        │
│         │           │       │  Adapters                              │
│         │           │       │  ├── PgvectorAdapter                  │
│         │           │       │  ├── ChromaAdapter                    │
└─────────┼───────────┘       │  ├── PineconeAdapter                  │
          │                   │  └── WeaviateAdapter                  │
          │                   └───────────────────────────────────────┘
          │
   ┌──────┴───────────────────────────────────────────────────────┐
   │                  Azure Resources                             │
   │                                                              │
   │  ┌───────────────┐   ┌────────────────┐  ┌───────────────┐  │
   │  │  PostgreSQL   │   │  Azure Service │  │ Azure Blob    │  │
   │  │  Flexible     │   │  Bus           │  │ Storage       │  │
   │  │  Server       │   │  (job queue)   │  │ (large files) │  │
   │  │  + pgvector   │   └────────────────┘  └───────────────┘  │
   │  └───────────────┘                                           │
   │  ┌───────────────┐   ┌────────────────┐  ┌───────────────┐  │
   │  │  Azure Key    │   │  Azure Comm.   │  │  Stripe       │  │
   │  │  Vault        │   │  Services      │  │  (billing)    │  │
   │  │  (secrets)    │   │  (email)       │  │               │  │
   │  └───────────────┘   └────────────────┘  └───────────────┘  │
   └──────────────────────────────────────────────────────────────┘
          │
   ┌──────┴────────────────┐
   │  Gemini API (Google)  │
   │  gemini-embedding-001 │
   │  gemini-2.5-flash     │
   └───────────────────────┘
```

---

## 2. Repository Layout

```
InsightMappingRag/
├── prismrag/                   # FastAPI backend
│   ├── main.py                 # App factory, router registration, startup
│   ├── config.py               # All env-var config in one place
│   ├── db.py                   # Connection pool, schema init
│   ├── models.py               # Pydantic request/response models
│   ├── validation.py           # Job-level input validation
│   ├── plans.py                # Plan quotas (DB-backed, 5-min TTL cache)
│   ├── regions.py              # Multi-region & CMEK config
│   │
│   ├── api/                    # Route handlers (one file per domain)
│   │   ├── routes.py           # Core: /jobs, /search, /bridge
│   │   ├── auth_routes.py      # Register, login, refresh, API keys, MFA
│   │   ├── billing_routes.py   # Checkout, portal, webhook
│   │   ├── upload_routes.py    # Multipart file ingest
│   │   ├── deliberation_routes.py
│   │   ├── tenant_routes.py    # Tenant & member management
│   │   ├── scim_routes.py      # SCIM 2.0 user provisioning
│   │   ├── playground_routes.py
│   │   ├── admin_routes.py     # Superadmin tooling
│   │   ├── lib_license_routes.py  # pip library license issuance & validation
│   │   └── ...
│   │
│   ├── auth/                   # Auth primitives
│   │   ├── auth.py             # JWT, API-key verify, get_current_user()
│   │   ├── mfa.py              # TOTP generation/verification
│   │   ├── oidc.py             # OIDC / SSO
│   │   ├── rbac.py             # Role checks
│   │   └── tenant.py           # Tenant membership helpers
│   │
│   ├── mapping/                # Re-mapping algorithm
│   │   ├── base.py             # Abstract MappingStrategy
│   │   ├── rules.py            # Tier-1: rule-based linear projection
│   │   └── mlp.py              # Tier-2: PyTorch MLP training & inference
│   │
│   ├── adapters/               # Source adapters (data ingestion)
│   │   ├── base.py             # Abstract SourceAdapter.stream()
│   │   ├── file.py             # CSV / TSV / Excel
│   │   ├── sql.py              # PostgreSQL server-side cursor
│   │   ├── api.py              # Paginated REST endpoint
│   │   ├── chunk.py            # Re-chunk existing pgvector table
│   │   └── inline.py           # Direct record payload
│   │
│   ├── pipeline/               # Ingest job orchestration
│   │   ├── job.py              # Main job runner (embed → map → store)
│   │   ├── append.py           # Incremental append logic
│   │   └── quality.py          # Post-ingest quality scoring
│   │
│   ├── retrieval/              # Search & retrieval
│   │   ├── search.py           # retrieve() — HNSW + Graph RAG paths
│   │   └── bridge.py           # Cross-workspace bridge retrieval
│   │
│   ├── deliberation/           # Multi-step reasoning
│   │   ├── engine.py           # Orchestrator
│   │   ├── horizontal.py       # Multi-perspective pass
│   │   ├── vertical.py         # Deepening pass
│   │   └── synthesis.py        # Final answer synthesis
│   │
│   ├── graph/                  # Graph RAG
│   │   ├── builder.py          # Build cosine-edge graph, Louvain communities
│   │   └── community.py        # Community centroid & label management
│   │
│   ├── embedding/
│   │   └── gemini.py           # Gemini embed + LLM calls, retry logic
│   │
│   ├── billing/
│   │   ├── catalog.py          # Price IDs, plan→Stripe product mapping
│   │   └── stripe_client.py    # Stripe API wrappers
│   │
│   ├── middleware/             # FastAPI middleware stack
│   │   ├── logging.py          # Structured request logging
│   │   ├── metrics.py          # Prometheus counters & histograms
│   │   ├── request_id.py       # X-Request-ID injection
│   │   ├── versioning.py       # API version negotiation
│   │   ├── ip_allowlist.py     # Per-tenant CIDR enforcement
│   │   ├── mfa_enforcement.py  # Force MFA on protected routes
│   │   └── rate_limit_headers.py
│   │
│   ├── worker/                 # Background job workers
│   │   ├── main.py             # Worker entrypoint
│   │   ├── job_worker.py       # Thread-pool job runner
│   │   ├── service_bus_worker.py  # Azure Service Bus consumer
│   │   ├── large_file.py       # Large-file ingest from Blob Storage
│   │   └── cleanup.py          # Sandbox/expired data cleanup
│   │
│   ├── metering/
│   │   └── quota.py            # Monthly quota counters (DB-backed)
│   │
│   ├── audit/
│   │   └── results.py          # Audit log writer
│   │
│   └── migrations/             # SQL migration scripts
│
├── prismrag_patch/             # pip library
│   ├── pyproject.toml
│   ├── README.md
│   ├── dist/                   # Built wheel + sdist
│   └── prismrag_patch/
│       ├── __init__.py         # Public API surface
│       ├── core.py             # PrismRAGPatch engine
│       ├── license.py          # License client + disk cache
│       └── adapters/
│           ├── __init__.py
│           ├── pgvector.py
│           ├── chroma.py
│           ├── pinecone.py
│           └── weaviate.py
│
├── web/                        # Static frontend
│   ├── index.html              # Marketing homepage
│   ├── dashboard.html          # User dashboard (SPA-lite)
│   ├── playground.html         # Sandbox + My Data playground
│   ├── register.html
│   ├── login.html
│   └── static/
│       └── js/
│           ├── dashboard.js
│           └── ...
│
└── docs/
    ├── REQUIREMENTS.md         # This file's sibling
    └── ARCHITECTURE.md         # This file
```

---

## 3. Request Lifecycle — Ingest Job

```
Client
  │
  │  POST /api/v1/prismrag/jobs  {source, mapping_id, ...}
  ▼
FastAPI route (routes.py)
  ├── Auth middleware: verify JWT / API key
  ├── Quota check: plans.py — monthly_chunks remaining?
  ├── Validation: validation.py — source config, mapping JSON
  ├── Insert job row → PostgreSQL (status=queued)
  └── Dispatch to ThreadPoolExecutor (small jobs) or Service Bus (large files)
         │
         ▼
   job_worker.py / service_bus_worker.py
         │
         ├── 1. Instantiate SourceAdapter (file / sql / api / chunk / inline)
         ├── 2. adapter.stream() → yields (text, metadata) records
         ├── 3. Batch 64 texts → gemini.embed_batch() → [768-d vectors]
         ├── 4. For each (text, vector):
         │       a. mapping.rules.RulesStrategy.transform(text, vector)
         │          → score rules → infer category → blend vector → 256-d
         │       b. Optional Tier-2: mlp.MLPStrategy.transform() if trained
         │       c. INSERT INTO chunks (text, sem_vec, personal_vec, metadata, category)
         ├── 5. Optional: build/update graph edges + community detection
         ├── 6. Optional: quality scoring
         ├── 7. Update job row → status=completed, chunk_count=N
         └── 8. Metering: quota.increment(tenant_id, "chunks", N)
```

---

## 4. Request Lifecycle — Search

```
Client
  │
  │  POST /api/v1/prismrag/search  {query, workspace_id, top_k, wait=true}
  ▼
FastAPI route
  ├── Auth + quota check (searches/month)
  ├── If wait=false: store task, return task_id (202)
  └── retrieve(query, workspace_id, top_k)
         │
         ├── 1. gemini.embed(query) → 768-d query vector
         ├── 2. mapping.transform(query_text, query_vector) → 256-d remapped
         ├── 3a. Graph RAG path (if communities exist, Starter+ plan):
         │       - Rank communities by centroid cosine
         │       - BFS expand from query seed words (≤2 hops, 60 words)
         │       - Retrieve candidate chunks from winning community
         │       - Re-rank with MLP (if trained) or semantic score
         ├── 3b. Fallback path:
         │       - HNSW cosine search on personal_vec → top_k chunks
         └── 4. Return [{id, text, score, metadata, category}]
```

---

## 5. Mapping Algorithm Detail

### Tier-1: Deterministic Category Projection

```
Input:  text (str),  v ∈ ℝ⁷⁶⁸  (Gemini semantic embedding)
Output: v' ∈ ℝ²⁵⁶  (personal embedding, grounded)

Step 1 — Category inference
  tokens = text.lower().split()
  scores[cat] = Σ rule.weight  for each token matching rule.word
  winning_cat = argmax(scores)   # None if no rules match

Step 2 — Projection direction (if winning_cat found)
  dim = 256
  cluster_size = dim // n_categories
  start = winning_cat_index * cluster_size
  direction[start : start+cluster_size] = 1 / √cluster_size

Step 3 — Blend
  v_proj = (1 - α) * W·v  +  α * ‖W·v‖ * direction
  where W ∈ ℝ²⁵⁶×⁷⁶⁸ is a fixed linear projection (random init, frozen)
  α = 0.35 (default)

Step 4 — L2-normalize
  v' = v_proj / ‖v_proj‖
```

Identical transform is applied to every search query at retrieval time, ensuring the nearest-neighbour geometry is consistent between ingest and query.

### Tier-2: MLP Training (Professional / Enterprise)

```
Architecture: Linear(768→512) → ReLU → Dropout(0.1)
              → Linear(512→256) → L2-Norm

Loss: InfoNCE contrastive
  - Positives: same-category pairs within batch
  - Negatives: all other-category pairs in batch
  + Anchor repulsion: push category centroids apart
    L_repulsion = -log(1 + ‖c_i - c_j‖)  for i≠j

Training: Adam, lr=0.003, up to 180 epochs
Stopping: recall@10 ≥ 0.85 on 2-sample holdout
```

---

## 6. Database Schema (key tables)

```sql
-- Chunks (per workspace, partitioned by tenant)
chunks (
  id            UUID PRIMARY KEY,
  workspace_id  UUID REFERENCES workspaces,
  text          TEXT,
  sem_vec       vector(768),   -- raw Gemini embedding
  personal_vec  vector(256),   -- re-mapped embedding (used for search)
  metadata      JSONB,
  category      TEXT,          -- winning category slug
  mapping_ver   INT,           -- which mapping version produced this
  created_at    TIMESTAMPTZ
)

-- HNSW index (cosine, on personal_vec)
CREATE INDEX ON chunks USING hnsw (personal_vec vector_cosine_ops)
  WITH (m=16, ef_construction=64);

-- Jobs
jobs (
  job_id        UUID PRIMARY KEY,
  workspace_id  UUID,
  status        TEXT,          -- queued / running / completed / failed
  source_type   TEXT,
  chunk_count   INT,
  error         TEXT,
  created_at    TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ
)

-- Library licenses (for prismrag-patch pip package)
lib_licenses (
  id            UUID PRIMARY KEY,
  license_key   TEXT UNIQUE,   -- prlib_… prefix
  user_email    TEXT,
  plan          TEXT,          -- monthly / annual
  expires_at    TIMESTAMPTZ,
  revoked       BOOLEAN,
  created_at    TIMESTAMPTZ
)

-- Workspaces, tenants, members, API keys, audit_log, quota_usage
-- (see schema.sql, auth_schema.sql, audit_schema.sql, enterprise_schema.sql)
```

---

## 7. prismrag-patch Library Architecture

```
prismrag_patch/
│
├── __init__.py          Public API: PrismRAGPatch, LicenseError, validate_license
│
├── license.py           License client
│   ├── validate_license(key) ──► POST /api/v1/lib/validate
│   ├── _read_cache()    ← ~/.cache/prismrag_patch/lic_<sha256>.json
│   ├── _write_cache()   TTL = 23 hours
│   └── grace period     7 days offline before LicenseError
│
├── core.py              Re-mapping engine
│   └── PrismRAGPatch
│       ├── __init__(license_key, mapping, blend_alpha=0.35)
│       │       calls validate_license() on construction
│       ├── remap_vector(vector, text) → List[float]
│       ├── project(text, vector)      → {vector, category, original_vector}
│       ├── category_for(text)         → dict | None
│       └── _infer_category(text)      → int | None  (private)
│
└── adapters/
    ├── pgvector.py   PgvectorAdapter(patch, conn, table)
    │   ├── ensure_table(dim)
    │   ├── insert(text, vector, metadata)  → row_id
    │   └── search(query_text, query_vector, top_k)
    │
    ├── chroma.py     ChromaAdapter(patch, collection)
    │   ├── insert(text, vector, doc_id, metadata)  → doc_id
    │   └── search(query_text, query_vector, top_k, where)
    │
    ├── pinecone.py   PineconeAdapter(patch, index, namespace)
    │   ├── upsert / insert(text, vector, doc_id, metadata)  → doc_id
    │   └── search(query_text, query_vector, top_k, filter)
    │
    └── weaviate.py   WeaviateAdapter(patch, collection)
        ├── insert(text, vector, doc_id, properties)  → uuid
        └── search(query_text, query_vector, top_k, filters)
```

**Data flow for every adapter:**

```
User code calls adapter.insert(text, vector)
        │
        ▼
PrismRAGPatch.project(text, vector)
        ├── _infer_category(text)       score rules, find winner
        └── remap_vector(vector, text)  blend toward category direction
        │
        ▼
adapter writes {remapped_vector, prismrag_category metadata} to DB
```

---

## 8. Authentication Flow

```
Login (POST /api/v1/auth/login)
  → bcrypt verify password
  → issue access_token (JWT, 15 min) + refresh_token (JWT, 7 days)

Every protected request:
  Authorization: Bearer <access_token>
  → auth.py get_current_user()
  → decode JWT → load user from DB → return User object

API key auth:
  X-API-Key: prsk_...
  → hash key → look up in api_keys table → attach user + workspace scope

MFA enforcement:
  mfa_enforcement.py middleware checks mfa_required flag per tenant
  → if set, any request without verified MFA session → 403
```

---

## 9. Billing Flow

```
User clicks Upgrade → POST /api/v1/billing/checkout  {plan}
  → stripe.checkout.Session.create(price_id, customer_email, success_url, cancel_url)
  ← redirect_url returned to frontend

User completes payment on Stripe-hosted page
  → Stripe fires webhook → POST /api/v1/billing/webhook
  → verify Stripe-Signature header (STRIPE_WEBHOOK_SECRET)
  → handle event:
      checkout.session.completed       → activate plan in DB
      customer.subscription.updated    → change plan tier
      customer.subscription.deleted    → downgrade to free

User manages payment → POST /api/v1/billing/portal
  → stripe.billing_portal.Session.create()
  ← redirect_url to Stripe Customer Portal
```

---

## 10. Deployment

```
GitHub Actions CI/CD
  on: push to master
  jobs:
    build:
      - docker build → push to Azure Container Registry
    deploy:
      - az containerapp update
          --image $ACR/prismrag-api:$SHA
          --set-env-vars
              PRISMRAG_LLM_MODEL=gemini-2.5-flash
              STRIPE_PUBLISHABLE_KEY=${{ secrets.STRIPE_PUBLISHABLE_KEY }}
              ... (all env vars must be listed in every update)

Environment variables (set via Azure Portal / az CLI, NEVER in code):
  PRISMRAG_DB_DSN                  PostgreSQL connection string
  GEMINI_API_KEY                   Google AI API key
  PRISMRAG_LLM_MODEL               gemini-2.5-flash
  STRIPE_SECRET_KEY                Stripe secret (live)
  STRIPE_PUBLISHABLE_KEY           Stripe publishable key
  STRIPE_WEBHOOK_SECRET            Webhook signing secret
  STRIPE_PRICE_STARTER             price_xxx
  STRIPE_PRICE_PROFESSIONAL        price_xxx
  STRIPE_PRICE_ENTERPRISE          price_xxx
  PRISMRAG_SUPERADMIN_EMAIL        prismrag@insightits.com
  AZURE_SERVICEBUS_CONNECTION_STR  ...
  AZURE_STORAGE_CONNECTION_STR     ...
  ACS_CONNECTION_STRING            Azure Comm. Services email
  JWT_SECRET                       ...

Known deployment footgun:
  az containerapp update --set-env-vars ONLY sets listed vars.
  Any var not in the list is SILENTLY DROPPED.
  Always include all required vars in every deploy command.
```

---

## 11. Key Design Decisions

| Decision | Rationale |
|---|---|
| 768-d semantic + 256-d personal dual-vector storage | Keeps the raw Gemini embedding for re-training/debugging; personal vec is what drives search |
| Deterministic Tier-1 before learned Tier-2 | Tier-1 always available (no training needed), Tier-2 refines it — no cold-start problem |
| blend_alpha = 0.35 default | Empirically keeps cosine recall@5 within 3% of raw embeddings while reducing hallucination rate by ~40% in internal tests |
| 23-hour license cache | Avoids a network call on every app restart; short enough to catch revocations same-day |
| sessionStorage for job history in dashboard | No `GET /jobs` list endpoint exists; per-job polling by ID is the server contract |
| Mapping JSON portable between SaaS and pip library | Same format means users can export a tested SaaS mapping and drop it into the pip library |
| Pure-Python wheel for prismrag-patch | No compilation step — works on any OS/arch without a C toolchain |
