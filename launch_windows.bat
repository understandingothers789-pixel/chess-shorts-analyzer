@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run PowerShell: .\install_windows.ps1
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m chess_shorts
if errorlevel 1 pause
