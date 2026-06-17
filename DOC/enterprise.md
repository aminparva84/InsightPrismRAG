# PrismRAG — Enterprise Readiness (v1.0)

Updated after enterprise hardening pass. See status below.

## Implemented in v1.0

| Capability | Status |
|------------|--------|
| **API versioning** | `/api/v1/*` primary; legacy `/api/*` auto-rewritten |
| **RBAC** | `tenant_member` roles: owner, admin, member, viewer |
| **Member management** | `GET/POST/DELETE /api/v1/tenants/{id}/members` |
| **Data export** | `GET /api/v1/tenants/{id}/export` |
| **Workspace delete** | `DELETE /api/v1/tenants/{id}` (owner only) |
| **OIDC / SSO** | `GET /api/v1/auth/oidc/login`, `/callback`, `/status` |
| **API key scopes** | `read`, `write` on key creation |
| **Unified plan limits** | `prismrag/plans.py` ← `plan_quota` table |
| **Prometheus metrics** | `GET /metrics` |
| **Request tracing** | `X-Request-Id` header + `trace_id` in audit log |
| **Result audit logs** | `search_result_log`, `ingest_result_log` written async |
| **Postgres job worker** | `python -m prismrag.worker.job_worker` |
| **Service Bus worker** | `python -m prismrag.worker.service_bus_worker` |
| **MCP HTTP auth** | `PRISMRAG_MCP_HTTP_TOKEN` on `/mcp/*` |
| **MCP uses REST API** | All tools call `/api/v1/*` (no direct DB bypass) |

## Production environment

```bash
PRISMRAG_ENV=production
JWT_SECRET=<64-char-hex>
PRISMRAG_DB_DSN=postgresql://...
PRISMRAG_CORS_ORIGINS=https://app.example.com
REDIS_URL=redis://...
PRISMRAG_USE_JOB_QUEUE=true          # run job_worker process
GEMINI_API_KEY=...

# SSO (optional)
OIDC_ISSUER=https://login.microsoftonline.com/{tenant}/v2.0
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=https://app.example.com/api/v1/auth/oidc/callback

# MCP HTTP mode
PRISMRAG_MCP_HTTP_TOKEN=<secret>
PRISMRAG_API_KEY=prk_...
```

## RBAC permissions

| Role | read | write | admin | delete |
|------|------|-------|-------|--------|
| viewer | ✓ | | | |
| member | ✓ | ✓ | | |
| admin | ✓ | ✓ | ✓ | |
| owner | ✓ | ✓ | ✓ | ✓ |

## Still recommended for full enterprise sales

- SOC 2 / formal compliance program
- SCIM user provisioning
- MFA for password accounts
- Customer-managed encryption keys (CMEK)
- Multi-region data residency
- Formal SLA + status page
- OpenTelemetry distributed tracing (metrics exist; traces partial via request ID)

## Local development

```powershell
.\run.ps1
# Optional: add to .env
PRISMRAG_USE_JOB_QUEUE=true
```

Worker starts automatically when `PRISMRAG_USE_JOB_QUEUE=true` in `.env`.
