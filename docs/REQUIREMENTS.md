# PrismRAG — Product Requirements Document

**Version:** 0.1  
**Date:** 2026-06-18  
**Owner:** Insight IT Solutions (prismrag@insightits.com)

---

## 1. Overview

PrismRAG is a two-part product:

| Component | What it is |
|---|---|
| **PrismRAG SaaS** | Multi-tenant FastAPI platform hosted on Azure Container Apps. Accepts documents from any source, embeds them, applies hallucination-resistant re-mapping, and serves semantic search + deliberation via REST API. |
| **prismrag-patch** | Pip-installable Python library. Lets users apply PrismRAG's re-mapping algorithm to their own vector databases (pgvector, ChromaDB, Pinecone, Weaviate) without moving data to PrismRAG's cloud. |

Both products share the same license server and the same core mapping algorithm (Tier-1 deterministic category projection).

---

## 2. Personas

| Persona | Description | Primary touchpoint |
|---|---|---|
| **SaaS User** | Developer or analyst who ingests company data, configures mappings, and queries via dashboard or API | Web dashboard, REST API |
| **Library User** | Developer who already has a vector DB and wants to add hallucination resistance without re-ingesting data | `pip install prismrag-patch`, their own app code |
| **Super Admin** | Insight IT team (prismrag@insightits.com). Issues library licenses, manages plans, views all tenants | `/api/v1/admin/*`, `/api/v1/lib/licenses` |
| **Tenant Admin** | Customer with "admin" role within their workspace. Can invite members, configure SCIM, see billing | Dashboard enterprise tab |
| **Guest / Playground User** | Unauthenticated visitor who tries Sandbox mode in the Playground | `/playground.html` |

---

## 3. PrismRAG SaaS — Functional Requirements

### 3.1 Data Ingestion

| ID | Requirement |
|---|---|
| ING-01 | Accept data from CSV, TSV, and Excel (`.xlsx`) files up to the plan file-size limit |
| ING-02 | Accept data from a SQL query against any PostgreSQL database via connection string |
| ING-03 | Accept data from a paginated REST API endpoint (auto-follows `data`, `items`, `results` envelope keys) |
| ING-04 | Accept inline record payloads (no external source) |
| ING-05 | Accept a re-chunking job that re-maps an existing pgvector table using new mapping rules |
| ING-06 | All ingest jobs are asynchronous; caller receives a `job_id` immediately (HTTP 202) and polls `GET /api/v1/prismrag/jobs/{job_id}` |
| ING-07 | Each job is tenant-scoped; one tenant cannot access another's job or data |
| ING-08 | Job timeout is configurable (default 7200 s). Worker marks timed-out jobs as `failed` |
| ING-09 | Batch embed with Gemini (`gemini-embedding-001`, 768-d output) in batches of 64 |
| ING-10 | Ingest batch size for DB writes is 256 records |
| ING-11 | Large files (> threshold) route to Azure Service Bus for background worker processing |

### 3.2 Mapping / Re-Mapping Algorithm

| ID | Requirement |
|---|---|
| MAP-01 | **Tier-1 (rule-based):** Developer defines categories (slug + label) and explicit word→category rules with weights. At ingest time, each chunk's text is scored against rules; the winning category determines a deterministic projection direction. The 768-d embedding is blended toward that direction (default α = 0.35) and stored as the 256-d personal embedding. |
| MAP-02 | Tier-1 rules are stored as a JSON mapping in the tenant's workspace. Multiple mappings per workspace are allowed up to the plan limit. |
| MAP-03 | **Tier-2 (MLP):** Available on Professional and Enterprise plans. A 3-layer PyTorch MLP (768→512→256) is trained on the tenant's own data using InfoNCE contrastive loss with anchor repulsion between categories. Validation stops at recall@10 ≥ 0.85 or 180 epochs. |
| MAP-04 | If no rules match a chunk, the chunk is stored using its raw semantic embedding (safe fallback, no hallucination risk introduced). |
| MAP-05 | Query re-mapping is identical to ingest re-mapping — same category projection is applied to every search query before nearest-neighbour lookup. |
| MAP-06 | Mapping version is recorded alongside each chunk so re-ingest with a new mapping doesn't corrupt old results. |

### 3.3 Search & Deliberation

| ID | Requirement |
|---|---|
| SRCH-01 | `POST /api/v1/prismrag/search` — semantic search. Supports `wait=true` (synchronous, ≤ 30 s) or async (returns task ID, poll for result). |
| SRCH-02 | Default retrieval: cosine similarity over HNSW index (pgvector). Returns top-K hits with score, text, and metadata. |
| SRCH-03 | **Graph RAG (Starter+ plans):** Chunk graph is built with cosine-threshold edges (≥ 0.70). Louvain community detection groups related chunks. Retrieval ranks communities by centroid similarity then BFS-expands from seed words (max 2 hops, 60 words). |
| SRCH-04 | Deliberation engine (`POST /api/v1/prismrag/deliberate`) runs horizontal (multi-perspective) + vertical (deepening) reasoning passes over retrieved context before generating a final answer via Gemini LLM. |
| SRCH-05 | LLM model is `gemini-2.5-flash` (overridable via `PRISMRAG_LLM_MODEL` env var). |
| SRCH-06 | Bridge search (`POST /api/v1/prismrag/bridge`) — cross-workspace retrieval for Enterprise tenants with bridge vectors enabled. |

### 3.4 Multi-Tenancy

| ID | Requirement |
|---|---|
| MT-01 | Each tenant has one or more workspaces. All data, jobs, mappings, and API keys are workspace-scoped. |
| MT-02 | Tenant members have roles: `owner`, `admin`, `member`. Role-based access is enforced on every API route. |
| MT-03 | Enterprise tenants can provision SCIM 2.0 for automated user provisioning (Azure AD, Okta). |
| MT-04 | Multi-region deployment: `us-east` (default), `eu-west`, `ap-southeast`. Data stays in the selected region. |
| MT-05 | CMEK (Customer-Managed Encryption Keys): Enterprise tenants can supply their own Azure Key Vault key for data-at-rest encryption. |

### 3.5 Authentication & Security

| ID | Requirement |
|---|---|
| AUTH-01 | JWT-based session auth (access + refresh token pair, 15-min / 7-day TTL). |
| AUTH-02 | API key auth for machine-to-machine calls (prefix `prsk_`). Keys are hashed in the database. |
| AUTH-03 | TOTP-based MFA. Admin can enforce MFA for all workspace members. |
| AUTH-04 | OIDC / SSO: Enterprise tenants can configure their IdP. |
| AUTH-05 | IP allowlist: Tenant admin can restrict API access to a set of CIDR ranges. |
| AUTH-06 | Password reset via Azure Communication Services email. |
| AUTH-07 | All authentication events are written to the audit log with timestamp, IP, and user agent. |

### 3.6 Billing

| ID | Requirement |
|---|---|
| BILL-01 | Stripe Checkout for plan purchase (free → starter → professional → enterprise). |
| BILL-02 | Stripe Customer Portal for plan changes, invoice history, and payment method updates. |
| BILL-03 | Stripe Webhook (`/api/v1/billing/webhook`) processes `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`. |
| BILL-04 | Monthly quota limits are enforced at the API layer before any processing begins. Quota counters reset on the first of each month. |
| BILL-05 | Request-rate limiting: enforced per API key per minute according to plan tier. |

**Plan Matrix:**

| Feature | Free | Starter | Professional | Enterprise |
|---|---|---|---|---|
| Chunks / month | 5,000 | 50,000 | 500,000 | Unlimited |
| Searches / month | 500 | 20,000 | 150,000 | Unlimited |
| Req / min | 10 | 60 | 300 | Custom |
| Max workspaces | 1 | 3 | 20 | Unlimited |
| Max mappings | 1 | 5 | 20 | Unlimited |
| Max file size | 10 MB | 100 MB | 1 GB | 10 GB |
| Graph RAG | No | Yes | Yes | Yes |
| Tier-2 MLP | No | No | Yes | Yes |
| Bridge vectors | No | No | No | Yes |
| CMEK | No | No | No | Yes |
| SCIM | No | No | No | Yes |
| Audit log retention | 7 days | 30 days | 90 days | 365 days |
| Support | Community | Email | Priority | Dedicated SLA |

### 3.7 Playground

| ID | Requirement |
|---|---|
| PG-01 | **Sandbox mode** — available to any visitor (unauthenticated). Ephemeral workspace, auto-deleted on session end. Lets users experience ingest + search + deliberation without signing up. |
| PG-02 | **My Data mode** — available to logged-in users. Queries the user's real workspaces. |
| PG-03 | Playground UI is at `/playground.html`. Dashboard links to it in the sidebar. |

### 3.8 Observability

| ID | Requirement |
|---|---|
| OBS-01 | Prometheus metrics exposed at `/metrics`. |
| OBS-02 | Request IDs on every response header for distributed tracing. |
| OBS-03 | Structured audit log of all write operations and auth events. |
| OBS-04 | Job worker emits progress events (started, chunk N of M, completed, failed). |

---

## 4. prismrag-patch Library — Functional Requirements

### 4.1 Core Engine

| ID | Requirement |
|---|---|
| LIB-01 | `PrismRAGPatch(license_key, mapping, blend_alpha=0.35)` — constructor validates the license key against the PrismRAG license server before accepting any work. |
| LIB-02 | `remap_vector(vector, text)` — applies Tier-1 category projection to an existing embedding vector. Returns the blended vector as a plain Python list (same length as input). |
| LIB-03 | `project(text, vector)` — convenience wrapper: infers category from text, remaps vector, returns `{vector, category, original_vector}`. |
| LIB-04 | `category_for(text)` — returns the matched category dict or `None`. |
| LIB-05 | If no rules match the input text, the original vector is returned unchanged (safe no-op). |
| LIB-06 | `blend_alpha` controls the strength of re-mapping: 0 = no change, 1 = full projection. Default 0.35 balances semantic richness with grounding. |
| LIB-07 | Mapping format is identical to the SaaS mapping JSON: `{categories: [{slug, label}], rules: [{word, category_slug, weight}]}`. Users can export their SaaS mapping and use it directly in the library. |

### 4.2 License System

| ID | Requirement |
|---|---|
| LIC-01 | License keys have the prefix `prlib_`. |
| LIC-02 | On first use, the library calls `POST https://prismrag.insightits.com/api/v1/lib/validate` with `{license_key, product}`. |
| LIC-03 | A valid response is cached on disk (`~/.cache/prismrag_patch/lic_<hash>.json`) for 23 hours. No network call is made within the TTL. |
| LIC-04 | If the license server is unreachable and a prior valid response exists within 7 days, the library operates in offline grace mode. |
| LIC-05 | If the grace period is exceeded or the key is rejected, `LicenseError` is raised with a clear human-readable message and a link to the purchase page. |
| LIC-06 | Cache location is overridable via `PRISMRAG_CACHE_DIR` environment variable. |
| LIC-07 | License server URL is overridable via `PRISMRAG_LICENSE_URL` environment variable (for self-hosted or staging). |
| LIC-08 | License issuance is superadmin-only: `POST /api/v1/lib/licenses` (protected by `prismrag@insightits.com` check). |
| LIC-09 | License types: monthly, annual. Stored in PostgreSQL (`lib_licenses` table). |
| LIC-10 | The validate endpoint is public (no auth header required). It checks key prefix, expiry, and revocation status. |

### 4.3 Chunk Support

| ID | Requirement |
|---|---|
| CHK-01 | The library works on any granularity the user provides: full documents, fixed-size chunks, sentence-window chunks, or semantic chunks. The user is responsible for chunking before calling `insert`. |
| CHK-02 | Each adapter's `insert` call accepts a single `(text, vector)` pair plus optional metadata. Metadata is stored alongside the chunk in the target database. |
| CHK-03 | For batch ingestion, users call `insert` in a loop. Future versions will add a `batch_insert(records)` method. |
| CHK-04 | Metadata injected by the library: `prismrag_category` (slug), `prismrag_label` (human label). User metadata is preserved unchanged. |

### 4.4 Adapter — pgvector

| ID | Requirement |
|---|---|
| PG-01 | `PgvectorAdapter(patch, connection, table="prismrag_chunks")` |
| PG-02 | `ensure_table(dim=1536)` — creates the table and `vector` extension if they don't exist. |
| PG-03 | `insert(text, vector, metadata=None)` — re-maps, inserts, returns `row_id`. |
| PG-04 | `search(query_text, query_vector, top_k=5)` — re-maps query, does `<=>` cosine operator search, returns `[{id, text, metadata, score}]`. |
| PG-05 | Accepts any psycopg2-compatible connection (psycopg2, asyncpg wrapper, SQLAlchemy `raw_connection()`). |

### 4.5 Adapter — ChromaDB

| ID | Requirement |
|---|---|
| CH-01 | `ChromaAdapter(patch, collection)` |
| CH-02 | `insert(text, vector, doc_id=None, metadata=None)` — auto-generates UUID if no `doc_id`. Returns `doc_id`. |
| CH-03 | `search(query_text, query_vector, top_k=5, where=None)` — supports Chroma `where` filter on metadata. |
| CH-04 | Compatible with both local (`chromadb.Client()`) and hosted (`chromadb.HttpClient()`) instances. |

### 4.6 Adapter — Pinecone

| ID | Requirement |
|---|---|
| PIN-01 | `PineconeAdapter(patch, index, namespace="")` |
| PIN-02 | `upsert(text, vector, doc_id=None, metadata=None)` (also aliased as `insert`). Returns `doc_id`. |
| PIN-03 | `search(query_text, query_vector, top_k=5, filter=None)` — supports Pinecone metadata filter dict. |
| PIN-04 | Compatible with Pinecone v3 client (`pinecone-client>=3.0`). |

### 4.7 Adapter — Weaviate

| ID | Requirement |
|---|---|
| WV-01 | `WeaviateAdapter(patch, collection)` where `collection` is a Weaviate v4 `Collection` object. |
| WV-02 | `insert(text, vector, doc_id=None, properties=None)` — stores `text` as a property. Returns UUID string. |
| WV-03 | `search(query_text, query_vector, top_k=5, filters=None)` — uses `near_vector` search, supports Weaviate v4 `Filter` objects. |

### 4.8 Installation & Packaging

| ID | Requirement |
|---|---|
| PKG-01 | Package name: `prismrag-patch`, published to PyPI. |
| PKG-02 | Core install (`pip install prismrag-patch`) has minimal deps: `requests>=2.28`, `numpy>=1.24`. |
| PKG-03 | Adapter extras: `[pgvector]`, `[chroma]`, `[pinecone]`, `[weaviate]`, `[all]`. |
| PKG-04 | Python 3.9 – 3.12 supported. |
| PKG-05 | Pure Python wheel (`py3-none-any`) — no compiled extensions in the library itself. |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | API p99 latency < 500 ms for search on workspaces up to 1M chunks. |
| NFR-02 | Ingest throughput ≥ 500 chunks/sec on a 2-vCPU container. |
| NFR-03 | All secrets (Stripe keys, Gemini API key, DB password) via Azure Key Vault or environment variables. Never in source code or logs. |
| NFR-04 | No PII logged. Request bodies are not stored in audit logs. |
| NFR-05 | Zero-downtime deployments via Azure Container Apps revision traffic splitting. |
| NFR-06 | The license validation endpoint must respond in < 200 ms p99 to avoid blocking library startup. |

---

## 6. Out of Scope (v0.1)

- Streaming LLM responses (SSE)
- Native SDKs for Go, TypeScript, Java
- On-premises (self-hosted) deployment of the SaaS backend
- Tier-2 MLP in the pip library (server-side only for now)
- Graph RAG in the pip library (server-side only for now)
- Batch insert API for the pip library (planned v0.2)
