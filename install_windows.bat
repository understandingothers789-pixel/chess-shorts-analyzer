@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    echo Python 3.12 or newer is required. Install it from https://www.python.org/downloads/ including the Python launcher.
    goto :failed
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
    echo Python 3.12 or newer is required. Install it from https://www.python.org/downloads/
    goto :failed
)
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :failed
where ffmpeg >nul 2>&1
if errorlevel 1 echo NOTE: Install FFmpeg and ffprobe and add them to PATH to import local video. See README.md.
where ffprobe >nul 2>&1
if errorlevel 1 echo NOTE: ffprobe was not found on PATH. See README.md.
echo.
echo Setup complete. Double-click launch_windows.bat to open the app.
pause
exit /b 0
:failed
echo.
echo Setup failed. Read the message above, fix the problem, then run this installer again.
pause
exit /b 1
