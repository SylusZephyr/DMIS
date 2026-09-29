@echo off
REM One-click local setup for the DMIS platform (Windows).
REM Double-click this file, or run it from a terminal. It is safe to re-run.
REM
REM   1. gets the engine branch (clones the repo next to this file if needed)
REM   2. creates a virtual environment (.venv) and installs dependencies
REM   3. builds the engine data from data\raw\ (writes database\dmie.duckdb)
REM   4. runs the engine smoke tests
REM   5. starts the platform (API + web app)
setlocal
set BRANCH=main
set REPO=https://github.com/SylusZephyr/DMIS-.git

where git >nul 2>nul || (echo [ERROR] Git is not installed: https://git-scm.com/download/win & pause & exit /b 1)
where python >nul 2>nul || (echo [ERROR] Python is not installed: https://www.python.org/downloads/ & pause & exit /b 1)

REM --- 1. code ---------------------------------------------------------------
if not exist "%~dp0.git" (
    if not exist "%~dp0DMIS\.git" (
        echo Cloning repository...
        git clone %REPO% "%~dp0DMIS" || (pause & exit /b 1)
    )
    cd /d "%~dp0DMIS"
) else (
    cd /d "%~dp0"
)

git diff --quiet && git diff --cached --quiet
if errorlevel 1 (
    echo [STOP] You have uncommitted changes. Commit or stash them, then re-run.
    git status --short
    pause & exit /b 1
)
echo Fetching branch %BRANCH%...
git fetch origin || (pause & exit /b 1)
git checkout %BRANCH% || (pause & exit /b 1)
git pull --ff-only origin %BRANCH% || (pause & exit /b 1)

REM --- 2. environment --------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    python -m venv .venv || (pause & exit /b 1)
)
set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pip >nul
echo Installing dependencies (first run takes a few minutes)...
%PY% -m pip install -e ".[dev,platform]"
if errorlevel 1 (
    echo [ERROR] Install failed. If scikit-learn/statsmodels tried to compile, install Python 3.12
    echo         from python.org, delete the .venv folder, and run this file again.
    pause & exit /b 1
)

REM --- 3. data ---------------------------------------------------------------
echo Building engine data from data\raw ...
%PY% scripts\run_engine.py --all-raw || (pause & exit /b 1)

REM --- 4. checks -------------------------------------------------------------
echo Running engine smoke tests...
%PY% -m pytest tests\engine -q || (echo [WARN] Some engine tests failed - see output above.)

REM --- 5. platform -----------------------------------------------------------
echo.
echo Setup complete. Starting the platform - close this window to stop it.
call run_platform.bat
