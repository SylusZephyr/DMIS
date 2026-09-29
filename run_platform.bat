@echo off
REM One-click: DMIS Intelligence Platform (Windows). Safe to re-run.
REM Installs Python + Node dependencies (first run), builds the data from data\raw\ (first run),
REM rebuilds the web apps when the code changed, then starts the API (port 8000), the new
REM interface v2 (http://localhost:3001) and the classic interface (http://localhost:3000).
REM Optional keys (AI, live Amazon data, Chinese marketplaces): copy .env.example to .env and fill in.
REM Close the three windows to stop.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] Install Python 3.11-3.12 from python.org & pause & exit /b 1)
where npm >nul 2>nul || (echo [ERROR] Install Node.js 20+ from nodejs.org & pause & exit /b 1)
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not "%%b"=="" set "%%a=%%b"
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
set PY=.venv\Scripts\python.exe
%PY% -m pip install -q -e ".[platform]" || (pause & exit /b 1)
if not exist "data\platform\business.db" (
    echo Building platform data from data\raw ...
    %PY% scripts\dmis.py bootstrap || (pause & exit /b 1)
)
set COMMIT=unknown
for /f %%c in ('git rev-parse HEAD 2^>nul') do set COMMIT=%%c
call :build frontend-v2 || (pause & exit /b 1)
call :build frontend || (pause & exit /b 1)

start "DMIS API" %PY% scripts\dmis.py serve --port 8000
start "DMIS classic UI" /d "%~dp0frontend" cmd /c "npm run start -- -p 3000"
start "DMIS UI v2" /d "%~dp0frontend-v2" cmd /c "npm run start -- -p 3001"
timeout /t 8 >nul
start http://localhost:3001
echo DMIS is running:
echo   new interface (v2): http://localhost:3001
echo   classic interface:  http://localhost:3000
echo   API docs:           http://localhost:8000/docs
exit /b 0

:build
REM install + build a web app when it has never been built or the code changed since its last build
pushd %1
if not exist "node_modules" (call npm install || (popd & exit /b 1))
set LAST=
if exist ".next\.dmis_commit" set /p LAST=<".next\.dmis_commit"
if not exist ".next\BUILD_ID" set LAST=
if not "!LAST!"=="%COMMIT%" (
    call npm install || (popd & exit /b 1)
    call npm run build || (popd & exit /b 1)
    echo %COMMIT%> ".next\.dmis_commit"
)
popd
exit /b 0
