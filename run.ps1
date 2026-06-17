# InsightPrismRAG - start locally (run from PowerShell or Terminal)
# Usage:  cd c:\code\InsightMappingRag
#         .\run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PythonSys = "C:\Users\parva\AppData\Local\Programs\Python\Python312\python.exe"
$Docker    = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$VenvPy    = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Port      = 8001

if (-not (Test-Path $PythonSys)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonSys = $cmd.Source
    } else {
        throw "Python not found. Install from https://www.python.org/downloads/"
    }
}

if (-not (Test-Path $VenvPy)) {
    Write-Host "[1/5] Creating virtual environment..." -ForegroundColor Cyan
    & $PythonSys -m venv .venv
} else {
    Write-Host "[1/5] Virtual environment found." -ForegroundColor Green
}

Write-Host "[2/5] Installing dependencies (first run may take several minutes)..." -ForegroundColor Cyan
& $VenvPy -m pip install --upgrade pip -q
& $VenvPy -m pip install -r requirements.txt -q

if (-not (Test-Path ".env")) {
    Write-Host "[3/5] Creating .env from .env.example..." -ForegroundColor Cyan
    Copy-Item .env.example .env
} else {
    Write-Host "[3/5] Using .env" -ForegroundColor Green
}

$envLine = Get-Content .env | Where-Object { $_ -match '^\s*PRISMRAG_PORT=' } | Select-Object -First 1
if ($envLine -and ($envLine -match '=\s*(\d+)')) {
    $Port = [int]$Matches[1]
}

if (Test-Path $Docker) {
    Write-Host "[4/5] Starting PostgreSQL (Docker)..." -ForegroundColor Cyan
    Write-Host "      Make sure Docker Desktop is running." -ForegroundColor DarkGray
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Docker compose up -d --wait 2>&1 | Out-Null
    $dockerOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if ($dockerOk) {
        Write-Host "      Database ready on localhost:5432" -ForegroundColor Green
    } else {
        Write-Host "      [WARN] Database not started - UI still loads; auth/API need DB." -ForegroundColor Yellow
    }
} else {
    Write-Host "[4/5] Docker not found - skipping database." -ForegroundColor Yellow
}

$url = "http://localhost:$Port"
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " InsightPrismRAG running at $url" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Home:      $url/"
Write-Host "  Dashboard: $url/dashboard.html"
Write-Host "  API docs:  $url/docs"
Write-Host ""
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

Start-Process $url
& $VenvPy -m uvicorn main:app --reload --host 127.0.0.1 --port $Port
