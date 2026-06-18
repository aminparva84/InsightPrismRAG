# PrismRAG Production QA — seed Azure DB, run API tests, write report
#
# Usage:
#   .\scripts\run_production_qa.ps1
#   .\scripts\run_production_qa.ps1 -SkipSeed          # tests only (DB already seeded)
#   .\scripts\run_production_qa.ps1 -AzureDsn "postgresql://..."
#
# Env (from .env or Key Vault):
#   PRISMRAG_AZURE_DB_DSN  — Azure Postgres DSN for prismrag database
#   PRISMRAG_PROD_URL      — default https://prismrag.insightits.com

param(
    [switch]$SkipSeed,
    [string]$AzureDsn = "",
    [string]$ProdUrl  = "",
    [string]$ReportDir = "DOC/qa-reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

# Load .env
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match "^\s*[^#].*=.*" } | ForEach-Object {
        $parts = $_ -split "=", 2
        $key   = $parts[0].Trim()
        $val   = $parts[1].Trim()
        if (-not [System.Environment]::GetEnvironmentVariable($key)) {
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

$apiUrl = if ($ProdUrl) { $ProdUrl } elseif ($env:PRISMRAG_PROD_URL) { $env:PRISMRAG_PROD_URL } else { "https://prismrag.insightits.com" }
$apiUrl = $apiUrl.TrimEnd("/")

$dsn = if ($AzureDsn) { $AzureDsn } else { $env:PRISMRAG_AZURE_DB_DSN }
if (-not $dsn) { $dsn = $env:PRISMRAG_DB_DSN }

$qaEmail = if ($env:PRISMRAG_PROD_QA_EMAIL) { $env:PRISMRAG_PROD_QA_EMAIL } else { "qa-prod@insightits.com" }
$qaPass  = if ($env:PRISMRAG_PROD_QA_PASSWORD) { $env:PRISMRAG_PROD_QA_PASSWORD } else { "QaProdPass!2026#" }

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$reportFile = Join-Path $ReportDir "production-qa-$timestamp.txt"
$pytestLog  = Join-Path $ReportDir "pytest-$timestamp.log"

function Write-ReportLine([string]$Line) {
    Write-Host $Line
    Add-Content -Path $reportFile -Value $Line
}

Write-ReportLine "PrismRAG Production QA Report"
Write-ReportLine "Generated: $(Get-Date -Format o)"
Write-ReportLine "API URL:   $apiUrl"
Write-ReportLine "QA User:   $qaEmail"
Write-ReportLine ""

# Health check
Write-ReportLine "=== API Health ==="
try {
    $health = Invoke-RestMethod -Uri "$apiUrl/api/v1/prismrag/health" -TimeoutSec 15
    Write-ReportLine "  Status: $($health.status)"
} catch {
    Write-ReportLine "  [FAIL] Health check failed: $_"
    exit 1
}

# Seed Azure DB
if (-not $SkipSeed) {
    Write-ReportLine ""
    Write-ReportLine "=== Initializing + seeding Azure Postgres ==="
    if (-not $dsn -or $dsn -match "localhost") {
        Write-ReportLine "  [SKIP] No Azure DSN. Set PRISMRAG_AZURE_DB_DSN or pass -AzureDsn."
        Write-ReportLine "         Fetch from Key Vault: az keyvault secret show --vault-name kvinsightitsprod01 --name database-url -o tsv --query value"
    } else {
        $masked = $dsn -replace "://[^@]+@", "://<creds>@"
        Write-ReportLine "  DSN: $masked"
        python scripts/init_azure_schema.py --dsn $dsn
        if ($LASTEXITCODE -ne 0) {
            Write-ReportLine "  [FAIL] Schema init failed (exit $LASTEXITCODE)"
            exit 1
        }
        Write-ReportLine "  Schema OK"
        python tests/seed_qa_data.py --dsn $dsn --production --drop
        if ($LASTEXITCODE -ne 0) {
            Write-ReportLine "  [FAIL] Seed failed (exit $LASTEXITCODE)"
            exit 1
        }
        Write-ReportLine "  Seed OK"
    }
} else {
    Write-ReportLine ""
    Write-ReportLine "=== Seeding skipped (-SkipSeed) ==="
}

# Verify login
Write-ReportLine ""
Write-ReportLine "=== Auth verification ==="
python scripts/qa_setup_prod_user.py --url $apiUrl --verify-only 2>&1 | ForEach-Object { Write-ReportLine "  $_" }
if ($LASTEXITCODE -ne 0) {
    Write-ReportLine "  [WARN] Auth verify failed — user may need DB seed with --production"
}

# Run pytest
Write-ReportLine ""
Write-ReportLine "=== Running production API tests ==="
$env:PRISMRAG_TEST_URL      = $apiUrl
$env:PRISMRAG_TEST_EMAIL    = $qaEmail
$env:PRISMRAG_TEST_PASSWORD = $qaPass
$env:QA_SEEDED              = "1"

python -m pytest tests/test_production_api.py tests/test_smoke.py -v --tb=short --color=yes 2>&1 |
    Tee-Object -FilePath $pytestLog |
    ForEach-Object { Write-ReportLine $_ }

$exitCode = $LASTEXITCODE
Write-ReportLine ""
if ($exitCode -eq 0) {
    Write-ReportLine "=== RESULT: PASSED ==="
} else {
    Write-ReportLine "=== RESULT: FAILED (exit $exitCode) ==="
}
Write-ReportLine "Full log: $pytestLog"

exit $exitCode
