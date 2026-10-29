@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Double-click install_windows.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m chess_shorts
if errorlevel 1 pause
