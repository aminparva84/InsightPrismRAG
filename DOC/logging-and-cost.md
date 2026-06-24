# Logging, Blob Storage, and Cost Reduction

PrismRAG can log in three tiers — use **email for urgent issues** and **Azure Blob for file archives**, instead of paying Log Analytics to ingest every console line.

| Method | Cost | Best for |
|--------|------|----------|
| **Log Analytics** (current ACA default) | ~**$2.76/GB** ingested | Deep KQL queries, Azure-native dashboards |
| **Azure Blob log files** | ~**$0.02/GB**/month stored | Cheap archives, download and grep |
| **Email alerts** | ~$0.001/email via ACS | Errors and crashes to your inbox |

Azure Blob Storage is the equivalent of **AWS S3** for plain log files.

---

## 1. Email alerts (errors to your personal inbox)

Already built in. Set on the API/worker Container App:

```env
PRISMRAG_ADMIN_EMAILS=you@gmail.com,your-other@email.com
PRISMRAG_ALERT_MIN_SEVERITY=ERROR
```

You get HTML emails with stack traces when:

- Unhandled API exceptions (`main.py` handler)
- Startup failures (schema, critical config)
- Anything calling `alert_admin(..., severity=ERROR|CRITICAL)`

No code changes needed — just set the env var in Azure or GitHub Secrets and redeploy.

---

## 2. Blob file logging (S3-style log dumps)

The app can flush buffered log lines to Azure Blob as plain `.log` files.

### Enable

```env
PRISMRAG_LOG_LEVEL=WARNING          # production: less noise
PRISMRAG_LOG_BLOB_ENABLED=true
PRISMRAG_LOG_BLOB_CONTAINER=prismrag-logs
PRISMRAG_LOG_BLOB_FLUSH_SEC=300     # upload every 5 minutes (or when buffer full)
AZURE_STORAGE_CONNECTION_STRING=... # same account as large-file uploads
```

Or use `AZURE_STORAGE_ACCOUNT` + `AZURE_STORAGE_KEY` (see `.env.example`).

### Blob path layout

```
prismrag-logs/
  logs/
    2026-06-19/
      prismrag-api--0000032/
        143022_123456.log
        143522_789012.log
```

### Browse / download

**Azure Portal:** Storage account → Containers → `prismrag-logs` → `logs/`

**CLI:**

```powershell
az storage blob list `
  --account-name stinsightitsprod01 `
  --container-name prismrag-logs `
  --prefix logs/2026-06-19/ `
  --auth-mode login `
  -o table

az storage blob download `
  --account-name stinsightitsprod01 `
  --container-name prismrag-logs `
  --name "logs/2026-06-19/prismrag-api--0000032/143022_123456.log" `
  --file .\today.log `
  --auth-mode login
```

**Estimated cost:** 1 GB/month of logs ≈ **$0.02** storage vs **~$2.76** Log Analytics ingestion.

---

## 3. Turn down Log Analytics (biggest savings)

Container Apps currently ship **all stdout** to `prismrag-logs` workspace (~31 GB/month observed).

### Option A — Cap ingestion (keep workspace, stop runaway bills)

```powershell
az monitor log-analytics workspace update `
  -g prismrag-rg `
  -n prismrag-logs `
  --quota 0.5 `
  --retention-time 7
```

### Option B — Reduce what ACA sends (after blob logging is on)

Lower log level so stdout is quiet:

```env
PRISMRAG_LOG_LEVEL=WARNING
```

Most INFO lines (embed batches, poll loops) stop appearing in console → less Log Analytics ingestion.

### Option C — Detach Log Analytics from Container Apps environment

Once blob + email are working, you can stop forwarding container console logs to Log Analytics entirely (logs only in blob + `az containerapp logs show` for live tail).

This requires updating the managed environment — test in a maintenance window:

```powershell
# Verify blob logging works first, then consult current ACA docs for:
# az containerapp env update ... --logs-destination ...
```

Keep the workspace resource if other tools use it; an empty workspace costs ~$0.

---

## 4. Recommended production setup

```env
# Quiet console → less Log Analytics cost
PRISMRAG_LOG_LEVEL=WARNING

# Archive to blob (~pennies)
PRISMRAG_LOG_BLOB_ENABLED=true
PRISMRAG_LOG_BLOB_CONTAINER=prismrag-logs
AZURE_STORAGE_CONNECTION_STRING=<your storage connection string>

# Errors to your inbox
PRISMRAG_ADMIN_EMAILS=you@gmail.com
PRISMRAG_ALERT_MIN_SEVERITY=ERROR
```

Plus Azure CLI:

```powershell
az monitor log-analytics workspace update -g prismrag-rg -n prismrag-logs --quota 0.5 --retention-time 7
```

**Expected savings:** ~**$60–80/month** on Log Analytics alone.

---

## 5. What we do *not* recommend

| Approach | Why |
|----------|-----|
| Email every log line | ACS limits, spam, cost at scale |
| Email daily full log dump | Large attachments, often blocked |
| Disable all logging | No audit trail for compliance |

**Better pattern:** WARNING+ to blob files, ERROR+ to email, optional weekly manual download from blob.

---

## 6. Deploy checklist

1. Ensure storage account exists (e.g. `stinsightitsprod01` or dedicated `prismragstorage`).
2. Add `AZURE_STORAGE_CONNECTION_STRING` to GitHub Secrets / Container App secrets.
3. Set logging env vars on `prismrag-api` and `prismrag-worker`.
4. Cap Log Analytics quota (command above).
5. Redeploy with `[publish]`.
6. Trigger a test error → confirm email arrives.
7. Wait 5 minutes → confirm `.log` blob appears in `prismrag-logs` container.

---

*See also: cost architecture discussion in chat / future `DOC/cost-optimization.md`*
