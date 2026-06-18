# PrismRAG — SOC 2 & ISO 27001 Compliance Program Starter

This is the **organizational** layer. Product controls are in [`iso27001-control-mapping.md`](iso27001-control-mapping.md).

## What the product now provides

| Control area | Implementation |
|--------------|----------------|
| Access control | JWT, API keys, OIDC, RBAC, SCIM |
| MFA | TOTP + backup codes (`/api/v1/auth/mfa/*`) |
| Audit | `api_request_log`, search/ingest logs, `trace_id` |
| Tenant isolation | `tenant_id` + `assert_permission` |
| Data export/delete | `/api/v1/tenants/{id}/export`, `DELETE` |
| Email audit | `email_log` table |
| Status / SLA | `/status.html`, `/sla.html`, `/api/v1/status` |
| Multi-region | `data_region` on tenant/org; `GET /api/v1/auth/regions` |
| CMEK | `POST /api/v1/auth/organizations/cmek` + Azure Key Vault |
| SCIM | `/api/v1/scim/v2/Users` |

## What you still need (non-code)

### Policies (write + approve)

- [ ] Information Security Policy
- [ ] Acceptable Use Policy
- [ ] Incident Response Plan
- [ ] Business Continuity / Disaster Recovery Plan
- [ ] Change Management Policy
- [ ] Vendor Management Policy
- [ ] Data Retention & Deletion Policy
- [ ] Access Control Policy

Templates: use Vanta/Drata policy library or ISO 27001 consultant templates.

### SOC 2 Type II (US-focused)

1. Choose trust criteria: **Security** (required) + Availability + Confidentiality
2. Engage CPA firm or use Vanta + auditor network
3. **Observation period:** 3–6 months of evidence
4. Evidence from PrismRAG:
   - `GET /metrics` screenshots / Prometheus exports
   - `email_log`, `api_request_log` samples
   - Access reviews (export `tenant_member` quarterly)
   - Change log from GitHub PRs

### ISO 27001 (global)

1. Define ISMS scope document
2. Risk assessment (asset → threat → treatment)
3. Statement of Applicability from [`iso27001-control-mapping.md`](iso27001-control-mapping.md)
4. Internal audit
5. Stage 1 + Stage 2 with accredited body (BSI, SGS, etc.)

### Evidence checklist (monthly)

| Evidence | Source |
|----------|--------|
| User access review | `tenant_member` + SCIM logs |
| Failed login / MFA | `api_request_log` path `/auth/login` |
| Backup restore test | Postgres restore runbook |
| Vulnerability scan | `pip audit`, Dependabot, container scan |
| Incident drill | Tabletop exercise notes |
| Vendor review | Google, Stripe, Azure DPAs current |
| Uptime report | `/api/v1/status` + SLA page |

## Recommended tooling

| Tool | Purpose |
|------|---------|
| [Vanta](https://vanta.com) or [Drata](https://drata.com) | Continuous compliance automation |
| GitHub Advanced Security | Dependency + secret scanning |
| Azure Monitor / Datadog | Alerting on `/metrics` |
| Route 53 + ACS | Email deliverability |

## Timeline

| Month | Activity |
|-------|----------|
| 1–2 | Policies, risk register, gap remediation (MFA enforced for admins) |
| 3–4 | Vanta/Drata connected; evidence collection begins |
| 5–6 | Internal audit; pen test |
| 7–9 | SOC 2 observation / ISO Stage 1 |
| 10–12 | SOC 2 report / ISO Stage 2 certificate |

## Enterprise customer package

Deliver with enterprise deals:

1. Signed SLA (`web/sla.html` + custom order form)
2. DPA + subprocessors list (Google Gemini, Stripe, Azure ACS)
3. SCIM + OIDC setup guide
4. CMEK Key Vault configuration runbook
5. Data region selection (`data_region` on tenant create)

Contact: **prismrag@insightits.com**
