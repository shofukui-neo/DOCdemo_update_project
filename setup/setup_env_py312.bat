@echo off
rem ============================================================================
rem DOCdemo_update_project - Auto setup with Python 3.12.10
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

echo.
echo ==================================================
echo   DOCdemo_update_project automatic setup
echo   Python 3.12.10 environment
echo ==================================================
echo.

set "PROJECT_ROOT=%~dp0.."
pushd "%PROJECT_ROOT%"
set "PROJECT_ROOT=%CD%"
echo Project root: %PROJECT_ROOT%
echo.

rem ----- Administrator check -----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Administrator privileges not detected.
    echo           winget installation may fail without admin rights.
    echo.
)

rem ----- Python 3.12.10 detection -----
set "PYTHON_CMD="
set "PYTHON_VERSION="
set "GIT_USER="
set "GIT_EMAIL="

for /f "usebackq tokens=2 delims= " %%A in (`py -3.12 --version 2^>nul`) do set "PYTHON_VERSION=%%A"
if defined PYTHON_VERSION (
    if /I "%PYTHON_VERSION%"=="3.12.10" set "PYTHON_CMD=py -3.12"
)

if not defined PYTHON_CMD (
    for /f "usebackq tokens=2 delims= " %%A in (`python --version 2^>nul`) do set "PYTHON_VERSION=%%A"
    if defined PYTHON_VERSION (
        if /I "%PYTHON_VERSION%"=="3.12.10" set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo [1/5] Installing Python 3.12.10...
    winget install --id Python.Python.3.12 --version 3.12.10 -e --source winget --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo [ERROR] Python 3.12.10 installation failed.
        echo         Please install Python 3.12.10 manually and rerun.
        pause
        popd
        exit /b 1
    )
    set "PYTHON_CMD=py -3.12"
)

%PYTHON_CMD% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Failed to run Python 3.12.10.
    echo         Please check your Python installation.
    pause
    popd
    exit /b 1
)
echo [OK] Using %PYTHON_CMD%
%PYTHON_CMD% --version

echo.
echo [2/5] Creating virtual environment
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    echo   - .venv already exists
) else (
    %PYTHON_CMD% -m venv "%PROJECT_ROOT%\.venv"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        popd
        exit /b 1
    )
)

set "VENV_PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo.
echo [3/5] Installing package dependencies
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if %errorlevel% neq 0 (
    echo [ERROR] Failed to upgrade pip.
    pause
    popd
    exit /b 1
)

"%VENV_PY%" -m pip install -r "%PROJECT_ROOT%\requirements.txt"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install requirements.
    pause
    popd
    exit /b 1
)

echo.
echo [4/5] Installing Playwright browsers
"%VENV_PY%" -m playwright install
if %errorlevel% neq 0 (
    echo [WARNING] Playwright browser install failed.
    echo           You can rerun manually: "%VENV_PY%" -m playwright install
)

echo.
echo [5/5] Checking Git settings
for /f "delims=" %%A in ('git config --global user.name 2^>nul') do set "GIT_USER=%%A"
for /f "delims=" %%A in ('git config --global user.email 2^>nul') do set "GIT_EMAIL=%%A"

if not defined GIT_USER (
    echo Git user.name is not configured.
    set /p "GIT_USER=Enter git user.name (leave empty to skip): "
    if defined GIT_USER git config --global user.name "!GIT_USER!"
)
if not defined GIT_EMAIL (
    echo Git user.email is not configured.
    set /p "GIT_EMAIL=Enter git user.email (leave empty to skip): "
    if defined GIT_EMAIL git config --global user.email "!GIT_EMAIL!"
)

echo.
echo ==================================================
echo   Setup is complete.
echo ==================================================
echo.
echo Virtual environment: %PROJECT_ROOT%\.venv
echo Verify with:
echo   powershell -ExecutionPolicy Bypass -File "%~dp0check_env.ps1"
echo.
popd
pause
endlocal
