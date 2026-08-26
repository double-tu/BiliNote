@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%BillNote_frontend"
set "FRONTEND_PORT=3015"

call :load_frontend_port
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%/"

cd /d "%ROOT_DIR%"

if /i "%~1"=="backend" goto run_backend
if /i "%~1"=="frontend" goto run_frontend
if /i "%~1"=="check" set "CHECK_ONLY=1"

call :validate_project
if errorlevel 1 goto main_failed

call :find_python
if errorlevel 1 (
    echo [ERROR] Python 3 was not found.
    echo Install Python 3.11 or create backend\.venv first.
    goto main_failed
)

pushd "%BACKEND_DIR%"
call "%PYTHON_EXE%" %PYTHON_ARGS% -c "import main" >nul 2>&1
set "BACKEND_IMPORT_STATUS=%ERRORLEVEL%"
popd
if not "%BACKEND_IMPORT_STATUS%"=="0" (
    echo [ERROR] Backend dependencies are incomplete for:
    echo         "%PYTHON_EXE%" %PYTHON_ARGS%
    echo.
    echo Python 3.11 is recommended. Install the complete dependency set:
    echo         "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r backend\requirements.txt
    goto main_failed
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js 20 or newer first.
    goto main_failed
)

if not exist "%FRONTEND_DIR%\node_modules\.bin\vite.cmd" (
    echo [ERROR] Frontend dependencies are not installed.
    echo.
    echo Run this command first:
    echo         cd /d "%FRONTEND_DIR%" ^&^& npm install
    goto main_failed
)

if defined CHECK_ONLY (
    echo Preflight check passed.
    exit /b 0
)

echo Starting BiliNote backend and frontend...
start "BiliNote Backend" cmd.exe /d /k call "%~f0" backend
if errorlevel 1 (
    echo [ERROR] Failed to open the backend window.
    goto main_failed
)

start "BiliNote Frontend" cmd.exe /d /k call "%~f0" frontend
if errorlevel 1 (
    echo [ERROR] Failed to open the frontend window.
    goto main_failed
)

echo Waiting for the frontend at %FRONTEND_URL% ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(60); while ((Get-Date) -lt $deadline) { try { Invoke-WebRequest -UseBasicParsing -Uri '%FRONTEND_URL%' -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if errorlevel 1 (
    echo [WARN] The services were started, but the frontend was not ready within 60 seconds.
    echo        Check the Backend and Frontend windows for details.
    echo        Open %FRONTEND_URL% after startup completes.
    exit /b 0
)

start "" "%FRONTEND_URL%"
exit /b 0

:validate_project
if not exist "%BACKEND_DIR%\main.py" (
    echo [ERROR] Missing backend\main.py. Keep run.bat in the project root.
    exit /b 1
)
if not exist "%FRONTEND_DIR%\package.json" (
    echo [ERROR] Missing BillNote_frontend\package.json. Keep run.bat in the project root.
    exit /b 1
)
exit /b 0

:load_frontend_port
if not exist "%ROOT_DIR%.env" exit /b 0
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT_DIR%.env") do (
    if /i "%%A"=="VITE_FRONTEND_PORT" for /f "tokens=1" %%P in ("%%B") do set "FRONTEND_PORT=%%P"
)
exit /b 0

:find_python
set "PYTHON_EXE="
set "PYTHON_ARGS="

if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%BACKEND_DIR%\.venv\Scripts\python.exe"
    exit /b 0
)
if exist "%ROOT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
    exit /b 0
)
if exist "%BACKEND_DIR%\venv\Scripts\python.exe" (
    set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
    exit /b 0
)
if exist "%ROOT_DIR%venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT_DIR%venv\Scripts\python.exe"
    exit /b 0
)

rem Preserve compatibility with the Conda environment used by the original script.
where conda.exe >nul 2>&1
if not errorlevel 1 (
    call conda.exe run -n bili python -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=conda.exe"
        set "PYTHON_ARGS=run -n bili python"
        exit /b 0
    )
)

where conda.bat >nul 2>&1
if not errorlevel 1 (
    call conda.bat run -n bili python -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=conda.bat"
        set "PYTHON_ARGS=run -n bili python"
        exit /b 0
    )
)

where py.exe >nul 2>&1
if not errorlevel 1 (
    py.exe -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py.exe"
        set "PYTHON_ARGS=-3"
        exit /b 0
    )
)

where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python.exe"
        exit /b 0
    )
)

exit /b 1

:run_backend
title BiliNote Backend
echo ========================================
echo BiliNote Backend - http://127.0.0.1:8483
echo ========================================
echo.

call :find_python
if errorlevel 1 (
    echo [ERROR] Python 3 was not found.
    exit /b 1
)

cd /d "%BACKEND_DIR%"
call "%PYTHON_EXE%" %PYTHON_ARGS% main.py
set "SERVICE_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Backend stopped with exit code %SERVICE_EXIT_CODE%.
exit /b %SERVICE_EXIT_CODE%

:run_frontend
title BiliNote Frontend
echo ========================================
echo BiliNote Frontend - %FRONTEND_URL%
echo ========================================
echo.

cd /d "%FRONTEND_DIR%"
call npm.cmd run dev
set "SERVICE_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Frontend stopped with exit code %SERVICE_EXIT_CODE%.
exit /b %SERVICE_EXIT_CODE%

:main_failed
echo.
echo Startup cancelled. Fix the error above and run run.bat again.
if defined CHECK_ONLY exit /b 1
pause
exit /b 1
