# Architecture

`src/chess_shorts` is an installable Python package. Run `python -m chess_shorts` for the desktop application.

| Module | Responsibility |
|---|---|
| `capture/local.py` | Tool discovery, ffprobe presentation timestamps, VFR-aware sampling, one-pass FFmpeg selection and cancellation |
| `capture/window.py` | Windows DPI awareness, pointer-safe Win32 window enumeration, selected-client acquisition, normalized crop, live scheduling |
| `capture/youtube.py` | Strict URL normalization, optional Playwright playback readiness and seeks, explicit automation failure |
| `control.py` | Thread-safe cancellation, pause/resume, active monotonic clock and interruption history |
| `session.py` | Session creation/loading, atomic JSON writes, chronological frame registration and safe relative paths |
| `sheets.py` | Aspect-preserving contact sheets, labels, layout validation and allocation limits; transactional regeneration |
| `inspection.py` | Exact-pixel duplicate candidates and equal-size RGB difference |
| `export.py` | Atomic ZIP creation, timestamp index and analysis instructions |
| `ui.py`, `ui_widgets.py` | PySide6 three-panel UI, background operations, bounded asynchronous image loading, crop and zoom widgets |

Long-running capture, sheet generation, export, and duplicate scans run on a QThread. Workers communicate through queued Qt signals; QPixmaps are created on the GUI thread. Thumbnail generation is independent, and full-resolution image requests are limited to two workers plus one replaceable pending request. Images are not all retained in RAM. Native capture is continuous until stopped or the optional active-duration limit is reached; it skips missed capture deadlines instead of creating catch-up duplicates.

Each capture writes only into its own session. A completed PNG is atomically placed before metadata references it. Metadata saves use a temporary file, flush, fsync, and atomic replacement. Sheet generation stages a complete new set before replacing the old set; a failed generation keeps the previous sheets. The exporter stages a ZIP before replacing the destination. Originals are never modified by sheet settings, comparisons, or duplicate review.

Public capture backends can be tested without the desktop. Tests use synthetic footage; native scheduling tests inject an image-producing backend rather than pretending that Linux can exercise Win32 graphics capture. Playwright integration uses a real browser and an ordinary local HTML video with HTTP byte-range support. The desktop test exercises actual Qt signals, threads, image viewing, settings, and export offscreen.

No services, database, remote storage, telemetry, or external analysis API are required. Browser window sessions intentionally leave the source URL and media timestamps unknown. YouTube automation uses an isolated temporary browser context and doesn't read the user's Brave profile.
