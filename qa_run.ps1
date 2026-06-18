# PrismRAG QA Run Script
# Orchestrates: seed local DB → run tests → report
#
# Usage:
#   .\qa_run.ps1                      # run all tests against local API
#   .\qa_run.ps1 -Target prod         # run against prod Azure (no seed needed)
#   .\qa_run.ps1 -Drop                # drop + re-seed local DB before tests
#   .\qa_run.ps1 -Domain healthcare   # seed + test one domain only
#   .\qa_run.ps1 -SetupProdUser       # create/verify the prod QA user then exit
#   .\qa_run.ps1 -SeedOnly            # seed local DB and exit without running tests
#
# Prerequisites:
#   - PostgreSQL running locally  (PRISMRAG_DB_DSN in .env)
#   - Local API server running    (run-local.bat or uvicorn main:app --port 8001)
#   - Python venv activated       (.\venv\Scripts\Activate.ps1)

param(
    [ValidateSet("local", "prod")]
    [string]$Target = "local",

    [string]$Domain = "all",

    [switch]$Drop,
    [switch]$SeedOnly,
    [switch]$SetupProdUser,

    [string]$LocalUrl  = "http://localhost:8001",
    [string]$ProdUrl   = "https://prismrag-api.delightfuldesert-fc8896c5.eastus2.azurecontainerapps.io",

    [string[]]$PytestArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Load .env ─────────────────────────────────────────────────────────────────
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

$DSN = $env:PRISMRAG_DB_DSN
if (-not $DSN) { $DSN = "postgresql://prismrag:prismrag@localhost:5432/prismrag" }

# ── Setup prod user mode ───────────────────────────────────────────────────────
if ($SetupProdUser) {
    Write-Host "`n=== Setting up QA user in prod Azure ===" -ForegroundColor Cyan
    $env:PRISMRAG_PROD_URL = $ProdUrl
    python scripts/qa_setup_prod_user.py
    exit $LASTEXITCODE
}

# ── Local target: seed the database ──────────────────────────────────────────
if ($Target -eq "local") {
    Write-Host "`n=== Seeding local PostgreSQL ===" -ForegroundColor Cyan
    Write-Host "  DSN: $($DSN -replace '://.*@', '://<creds>@')"
    Write-Host "  Domain: $Domain"

    $seedArgs = @("tests/seed_qa_data.py", "--dsn", $DSN, "--domain", $Domain)
    if ($Drop) { $seedArgs += "--drop" }

    python @seedArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Seeding failed." -ForegroundColor Red
        exit 1
    }

    if ($SeedOnly) {
        Write-Host "`nSeed complete. Exiting (--SeedOnly)." -ForegroundColor Green
        exit 0
    }
}

# ── Determine API URL and credentials ────────────────────────────────────────
if ($Target -eq "prod") {
    $apiUrl   = $ProdUrl
    $qaEmail  = if ($env:PRISMRAG_PROD_QA_EMAIL)    { $env:PRISMRAG_PROD_QA_EMAIL }    else { "qa-prod@test.prismrag.io" }
    $qaPass   = if ($env:PRISMRAG_PROD_QA_PASSWORD) { $env:PRISMRAG_PROD_QA_PASSWORD } else { "QaProd!2024Secure" }
} else {
    $apiUrl   = $LocalUrl
    $qaEmail  = if ($env:PRISMRAG_TEST_EMAIL)    { $env:PRISMRAG_TEST_EMAIL }    else { "qa-local@test.prismrag.io" }
    $qaPass   = if ($env:PRISMRAG_TEST_PASSWORD) { $env:PRISMRAG_TEST_PASSWORD } else { "QaTestPass!123" }
}

$env:PRISMRAG_TEST_URL      = $apiUrl
$env:PRISMRAG_TEST_EMAIL    = $qaEmail
$env:PRISMRAG_TEST_PASSWORD = $qaPass

# ── Wait for API to be ready (local only) ─────────────────────────────────────
if ($Target -eq "local") {
    Write-Host "`n=== Waiting for local API at $apiUrl ===" -ForegroundColor Cyan
    $maxWait = 30
    $waited  = 0
    $ready   = $false
    while ($waited -lt $maxWait) {
        try {
            $resp = Invoke-WebRequest -Uri "$apiUrl/api/v1/prismrag/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
        $waited += 2
        Write-Host "  ...waiting ($waited`s)" -ForegroundColor Gray
    }
    if (-not $ready) {
        Write-Host "[WARN] API did not respond at $apiUrl after $maxWait`s. Tests may fail." -ForegroundColor Yellow
        Write-Host "       Start the server first: .\run-local.bat" -ForegroundColor Yellow
    } else {
        Write-Host "  API is ready." -ForegroundColor Green
    }
}

# ── Run pytest ────────────────────────────────────────────────────────────────
Write-Host "`n=== Running tests (target=$Target  url=$apiUrl) ===" -ForegroundColor Cyan

$pytestCmd = @(
    "-m", "pytest",
    "tests/",
    "--base-url=$apiUrl",
    "-v",
    "--tb=short",
    "--color=yes"
) + $PytestArgs

# Scope to domain-specific tests if requested
if ($Domain -ne "all") {
    $pytestCmd += "-k"
    $pytestCmd += $Domain
}

python @pytestCmd
$exitCode = $LASTEXITCODE

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "=== QA PASSED ===" -ForegroundColor Green
} else {
    Write-Host "=== QA FAILED (exit $exitCode) ===" -ForegroundColor Red
}

exit $exitCode
