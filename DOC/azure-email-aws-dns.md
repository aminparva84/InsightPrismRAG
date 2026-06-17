# Azure Email for PrismRAG@insightits.com (domain DNS in AWS)

Yes — **your domain can stay in AWS Route 53** while email sends from **Azure Communication Services (ACS)**. DNS is just records; they can point anywhere.

## Architecture

```text
insightits.com (Route 53 in AWS)
    ├── A/CNAME  → your app (Azure, Cloudflare, etc.)
    ├── MX       → optional if you only send (transactional), not receive
    ├── TXT      → SPF includes Azure
    ├── CNAME    → DKIM (Azure provides 2 records)
    └── CNAME    → Domain verification (Azure)

Azure Communication Services (Email)
    └── Verified sender: PrismRAG@insightits.com
```

PrismRAG app → `azure.communication.email` → recipient inbox

---

## Step 1 — Create Azure Communication Services

### Azure Portal

1. **Create resource** → **Communication Services**
2. Name: `prismrag-acs` (or your choice), region: same as app (e.g. East US)
3. After create: **Email** → **Provision domains** → **Add domain** → **Custom domain**
4. Enter: `insightits.com`
5. Azure shows DNS records to add (verification + DKIM)

### Azure CLI

```bash
az communication create --name prismrag-acs --resource-group prismrag-rg --location eastus
az communication email domain create \
  --domain-name insightits.com \
  --email-service-name prismrag-email \
  --resource-group prismrag-rg \
  --location global
```

---

## Step 2 — Add DNS records in AWS Route 53

1. AWS Console → **Route 53** → **Hosted zones** → `insightits.com`
2. Create each record Azure shows:

| Type | Name | Value | Purpose |
|------|------|-------|---------|
| TXT | `@` or `_azurecomm...` | (from Azure) | Domain verification |
| CNAME | `selector1._domainkey` | (from Azure) | DKIM 1 |
| CNAME | `selector2._domainkey` | (from Azure) | DKIM 2 |
| TXT | `@` | `v=spf1 include:spf.protection.outlook.com -all` | SPF (use exact string from Azure) |

3. Wait for Azure portal to show **Verified** (often 15–60 minutes)

---

## Step 3 — Configure sender address

1. ACS → **Email** → **MailFrom addresses** → Add `PrismRAG@insightits.com`
2. Or use subdomain: `noreply@insightits.com` (also fine)

---

## Step 4 — Connection string in PrismRAG

1. ACS resource → **Keys** → copy **Connection string**
2. Add to `.env`:

```bash
AZURE_COMMUNICATION_CONNECTION_STRING=endpoint=https://...;accesskey=...
PRISMRAG_EMAIL_FROM=PrismRAG@insightits.com
PRISMRAG_EMAIL_ENABLED=true
```

3. Restart API: `.\run.ps1`

---

## Step 5 — Test

```powershell
cd c:\code\InsightMappingRag
.\.venv\Scripts\python.exe -c "
from prismrag.email.azure_acs import send_email
print(send_email('you@example.com', 'PrismRAG test', '<p>Hello from PrismRAG</p>'))
"
```

Or register a new user — welcome email sends automatically.

Check `prismrag.email_log` table for delivery status.

---

## Receiving mail (optional)

ACS Email is **send-only** by default. To **receive** at `PrismRAG@insightits.com`:

- **Microsoft 365** with `insightits.com` in Exchange Online (MX in Route 53 → Microsoft), or
- **AWS SES** receiving + S3/Lambda (keep send on Azure or move all email to SES)

For transactional product email (welcome, MFA, invites), **send-only ACS is enough**.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Domain not verifying | Re-check TXT/CNAME in Route 53; no conflicting SPF TXT |
| Mail goes to spam | Complete DKIM; add DMARC TXT record |
| `ACS not configured` | Set `AZURE_COMMUNICATION_CONNECTION_STRING` |
| Sender not allowed | Add MailFrom address in ACS portal |

---

## DMARC (recommended)

Add in Route 53:

```text
Type: TXT
Name: _dmarc
Value: v=DMARC1; p=quarantine; rua=mailto:PrismRAG@insightits.com
```

---

## PrismRAG email templates

| Event | Template | Trigger |
|-------|----------|---------|
| Registration | `welcome` | `POST /api/v1/auth/register` |
| MFA enabled | `mfa_enabled` | `POST /api/v1/auth/mfa/enroll/confirm` |
| Member invite | `member_invite` | `POST /api/v1/tenants/{id}/members` |
| Custom | `generic` | `send_email()` API |

Sender display name is controlled in ACS / message headers; from address is `PRISMRAG_EMAIL_FROM`.
