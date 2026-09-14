@echo off
setlocal

cd /d "%~dp0"

echo === Silhouettes: setup venv + install dependencies ===

REM 1. Create the virtual environment (idempotent)
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        exit /b 1
    )
) else (
    echo Virtual environment already exists, reusing it.
)

REM 2. Upgrade pip inside the venv
".venv\Scripts\python.exe" -m pip install --upgrade pip

REM 3. Install project dependencies into the venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    exit /b 1
)

echo.
echo === Done. Activate with: .venv\Scripts\activate ===
echo === Run the app with:   .venv\Scripts\python app.py ===
endlocal
