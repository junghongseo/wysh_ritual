@echo off
:: Ensure the script runs in the directory where the .bat file is located
pushd "%~dp0"

echo ==================================================
echo      WYSH RITUAL AI Editor Engine Starting...
echo ==================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python and check 'Add Python to PATH'.
    pause
    exit /b
)

:: Check and create virtual environment if missing
IF NOT EXIST ".venv\Scripts\activate.bat" (
    echo [INFO] First time setup: Creating virtual environment... (Takes ~1 min)
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    pip install notion-client==2.2.1
    echo [INFO] Setup complete!
    echo.
) ELSE (
    call .venv\Scripts\activate.bat
)

:: Run the script
python ritual_engine.py

echo.
echo ==================================================
echo   Process Complete! Check your Notion Dashboard.
echo ==================================================
echo.

pause
