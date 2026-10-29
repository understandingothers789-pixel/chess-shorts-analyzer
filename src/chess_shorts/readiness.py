"""Read-only runtime diagnostics and capture sizing estimates."""
import math
import subprocess
import sys

from .capture.local import executable


def diagnostics():
    lines = []
    for tool in ('ffmpeg', 'ffprobe'):
        try:
            result = subprocess.run([executable(tool), '-version'], capture_output=True,
                                    text=True, timeout=10,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError('could not run the executable')
            lines.append(result.stdout.splitlines()[0])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            lines.append(f'{tool}: NOT READY — {exc}')
    try:
        if getattr(sys, 'frozen', False):
            lines.append('Optional Chromium: use the installation instructions to prepare its browser cache.')
            return '\n'.join(lines + ['Native window capture requires Windows 11; verify using capture preview.'])
        result = subprocess.run([sys.executable, '-m', 'playwright', 'install', '--list'],
                                capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        ready = result.returncode == 0 and 'chromium' in result.stdout.lower()
        lines.append('Optional automation Chromium: ' + ('listed in Playwright cache' if ready else
                     'not confirmed; run python -m playwright install chromium'))
    except (OSError, subprocess.TimeoutExpired) as exc:
        lines.append(f'Optional automation Chromium: unavailable — {exc}')
    lines.append('Native window capture: ' + ('available for Windows testing' if sys.platform == 'win32'
                                             else 'requires Windows 11'))
    return '\n'.join(lines)


def estimate(duration, interval, per_sheet, width=None, height=None):
    if not all(math.isfinite(v) and v > 0 for v in (duration, interval)) or per_sheet < 1:
        raise ValueError('Duration, interval, and frames per sheet must be positive.')
    count = math.ceil(duration / interval - 1e-9)
    result = f'≈ {count:,} requested frames · ≥ {math.ceil(count / per_sheet):,} sheets'
    if width and height:
        # Originals plus sheets; labels/spacing and intermediate files add overhead.
        mib = count * width * height * 3 * 2 / (1024 * 1024)
        result += f'\nUncompressed RGB + sheets ≈ {mib:,.0f} MiB; actual disk use varies.'
    return result
