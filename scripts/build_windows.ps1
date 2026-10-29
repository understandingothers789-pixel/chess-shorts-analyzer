$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Run install_windows.ps1 first.' }
& '.\.venv\Scripts\python.exe' -m pip install -e '.[package]'
if ($LASTEXITCODE -ne 0) { throw 'Packaging dependency installation failed.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --windowed --onedir --name ChessShortsStudio --paths src --collect-all playwright scripts/desktop_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
Copy-Item README.md dist/ChessShortsStudio/README.md
Copy-Item docs/limitations.md dist/ChessShortsStudio/LIMITATIONS.md
Copy-Item docs/THIRD_PARTY.md dist/ChessShortsStudio/THIRD_PARTY.md
Write-Host 'Build: dist\ChessShortsStudio\ChessShortsStudio.exe. Distribute the entire directory.'
Write-Host 'FFmpeg and optional Chromium are external prerequisites; see README.'
