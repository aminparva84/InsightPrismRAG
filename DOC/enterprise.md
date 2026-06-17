# PrismRAG — Enterprise Readiness (v1.1)

## Implemented

| Capability | Status |
|------------|--------|
| API v1 + legacy rewrite | ✅ |
| RBAC + member management | ✅ |
| OIDC / SSO | ✅ |
| **MFA (TOTP + backup codes)** | ✅ `/api/v1/auth/mfa/*` |
| **SCIM 2.0 provisioning** | ✅ `/api/v1/scim/v2/Users` |
| **Multi-region tenants** | ✅ `data_region` + `/api/v1/auth/regions` |
| **CMEK (Azure Key Vault)** | ✅ `/api/v1/auth/organizations/cmek` |
| **Azure email (ACS)** | ✅ `PrismRAG@insightits.com` via `prismrag/email/` |
| **Status page + SLA** | ✅ `/status.html`, `/sla.html`, `/api/v1/status` |
| Async ingest + search | ✅ job queue + search tasks |
| Prometheus + request tracing | ✅ |
| Compliance docs | ✅ `DOC/compliance-program.md`, `DOC/iso27001-control-mapping.md` |

## Still organizational (not code)

- SOC 2 / ISO **certificates** — see `DOC/compliance-program.md`
- Signed customer DPAs
- Penetration test report
- 24×7 on-call rotation

## Quick links

- Email setup (AWS DNS + Azure): [`DOC/azure-email-aws-dns.md`](azure-email-aws-dns.md)
- ISO control map: [`DOC/iso27001-control-mapping.md`](iso27001-control-mapping.md)
- Compliance program: [`DOC/compliance-program.md`](compliance-program.md)
