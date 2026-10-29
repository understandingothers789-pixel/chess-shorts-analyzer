param([switch]$WithBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Check-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)." }
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.12 or newer from python.org, including the Python launcher, then rerun this script.'
}
py -3 -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'"
Check-Exit 'Python version check'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    Check-Exit 'Virtual environment creation'
}
& '.\.venv\Scripts\python.exe' -m pip install -e .
Check-Exit 'Dependency installation'
if ($WithBrowser) {
    & '.\.venv\Scripts\python.exe' -m playwright install chromium
    Check-Exit 'Chromium installation'
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Warning 'Install FFmpeg and ffprobe before using local import. Suggested: winget install --id Gyan.FFmpeg --exact'
    Write-Warning 'Restart your terminal after installing FFmpeg so PATH is refreshed.'
}
Write-Host 'Installed. Launch with launch_windows.bat. All captures are saved locally.'
