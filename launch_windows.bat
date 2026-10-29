@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Double-click install_windows.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m chess_shorts
