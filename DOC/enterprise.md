# PrismRAG — Enterprise Readiness (v1.2)

## Implemented

| Capability | Status |
|------------|--------|
| API v1 + legacy rewrite | ✅ |
| RBAC + member management | ✅ |
| OIDC / SSO | ✅ login button + browser redirect callback |
| **MFA (TOTP + backup codes)** | ✅ API + login step + dashboard Security |
| **Password reset** | ✅ email link + forgot/reset pages |
| **SCIM 2.0 provisioning** | ✅ `/api/v1/scim/v2/Users` + dashboard token UI |
| **Multi-region tenants** | ✅ `data_region` + org creation UI |
| **CMEK (Azure Key Vault)** | ✅ API + dashboard configure UI |
| **Azure email (ACS)** | ✅ `PrismRAG@insightits.com` |
| **Status page + SLA** | ✅ `/status.html`, `/sla.html`, `/api/v1/status` |
| **List workspaces** | ✅ `GET /api/v1/prismrag/tenants` |
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
