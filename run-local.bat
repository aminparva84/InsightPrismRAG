@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ── Tool paths (detected on this machine) ──────────────────────────────────
set "PYTHON_SYS=C:\Users\parva\AppData\Local\Programs\Python\Python312\python.exe"
set "DOCKER=C:\Program Files\Docker\Docker\resources\bin\docker.exe"

if not exist "%PYTHON_SYS%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found at:
        echo         %PYTHON_SYS%
        echo         Install Python 3.11+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PYTHON_SYS=python"
)

if not exist "%DOCKER%" (
    where docker >nul 2>&1
    if errorlevel 1 (
        set "DOCKER="
    ) else (
        set "DOCKER=docker"
    )
)

echo.
echo  ============================================
echo   InsightPrismRAG - Local Launcher
echo  ============================================
echo.
echo  Python: %PYTHON_SYS%
if defined DOCKER echo  Docker: %DOCKER%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating virtual environment...
    "%PYTHON_SYS%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Virtual environment found.
)

call .venv\Scripts\activate.bat
set "PYTHON=.venv\Scripts\python.exe"

echo [2/5] Installing dependencies (first run may take several minutes)...
"%PYTHON%" -m pip install --upgrade pip -q
"%PYTHON%" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [3/5] Creating .env from .env.example...
    copy /Y .env.example .env >nul
) else (
    echo [3/5] Using existing .env file.
)

if not defined DOCKER (
    echo [4/5] Docker not found - skipping database container.
    echo        Install Docker Desktop for full features: https://www.docker.com/products/docker-desktop/
    echo        The landing page and API docs will still load.
    goto start_server
)

echo [4/5] Starting PostgreSQL with pgvector...
echo        Make sure Docker Desktop is running before this step.
"%DOCKER%" compose up -d --wait
if errorlevel 1 (
    echo [WARN] Could not start database container. Is Docker Desktop running?
    goto start_server
)
echo        Database ready on localhost:5432

:start_server
set PORT=8001
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /B "PRISMRAG_PORT=" .env 2^>nul`) do set PORT=%%B

echo [5/5] Starting server at http://localhost:%PORT%
echo.
echo  Open in browser:
echo    Home:      http://localhost:%PORT%/
echo    Dashboard: http://localhost:%PORT%/dashboard.html
echo    API docs:  http://localhost:%PORT%/docs
echo.
echo  Press Ctrl+C to stop the server.
echo.

start "" "http://localhost:%PORT%"
"%PYTHON%" -m uvicorn main:app --reload --host 127.0.0.1 --port %PORT%

echo.
echo Server stopped.
pause
