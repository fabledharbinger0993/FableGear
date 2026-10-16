@echo off
setlocal EnableDelayedExpansion

set SCRIPT_DIR=%~dp0
set VENV=%SCRIPT_DIR%venv
set LOG=%SCRIPT_DIR%fablegear.log

:: ── Check Python ──────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo FableGear: Python not found.
    echo.
    echo Install Python 3.11 or later from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Require Python 3.11+
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if !PY_MAJOR! LSS 3 (
    echo FableGear: Python 3.11+ required. Found !PY_VER!
    pause
    exit /b 1
)
if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 11 (
    echo FableGear: Python 3.11+ required. Found !PY_VER!
    pause
    exit /b 1
)

:: ── Create venv if missing ────────────────────────────────────────────────────
if not exist "%VENV%\Scripts\python.exe" (
    echo FableGear: Creating virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo FableGear: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: ── Install / upgrade dependencies ───────────────────────────────────────────
if not exist "%SCRIPT_DIR%.fablegear_ready" (
    echo FableGear: Installing dependencies ^(first run — this takes a minute^)...
    "%VENV%\Scripts\pip" install --upgrade --quiet -r "%SCRIPT_DIR%requirements.txt" >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo FableGear: Dependency install failed. Check fablegear.log for details.
        pause
        exit /b 1
    )
    echo. > "%SCRIPT_DIR%.fablegear_ready"
)

:: ── Launch FableGear ──────────────────────────────────────────────────────────
echo FableGear: Starting...
cd /d "%SCRIPT_DIR%"
start "" "%VENV%\Scripts\pythonw.exe" "%SCRIPT_DIR%main.py"
