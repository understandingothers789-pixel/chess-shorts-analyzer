# Validation report

Validated on 2026-10-29 in the Linux cloud workspace using Python 3.12.14, FFmpeg/ffprobe 7.1.5, Pillow 12.3.0, PySide6 6.12.0, and Playwright 1.62.0 with system Chromium.

## Final automated run

```sh
RUN_BROWSER_TESTS=1 TEST_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

**17 tests executed, 17 passed, no skips or expected failures** (8.687 seconds). The browser test required the cloud runner's supported permission path for loopback networking. TLS/package verification remained enabled for dependency installation.

| Area | Evidence |
|---|---|
| Local overview / export | Generated 30-second 30 fps MP4 → exactly 60 samples at 0.5 s → six sheets; dimensions, chronological timestamps, CSV, JSON, optional originals, ZIP integrity verified |
| Detailed capture | 10–12 seconds at 0.1 s → 20 samples → five four-frame sheets |
| Variable frame rate | Real encoded VFR clip plus independent irregular-timestamp selection test; actual recorded timestamps match source presentation times and no artificial duplicates are inserted |
| Desktop workflow | Actual offscreen Qt window; background capture, async thumbnails, full-resolution viewer, pin/compare/difference, export, settings persistence/reopen |
| Native scheduling logic | Injected window backend; resize-aware crop, four actual elapsed samples, pause/resume clock, stop, window-closure recovery; not a native Win32 test |
| Browser capture | Real Chromium seeks a generated HTML video to 0, 0.5, 1, and 1.5 s and creates four screenshots; explicit advertisement state produces a saved error session |
| Reliability | Corrupt/missing video, missing tool, invalid URLs, unsafe paths, invalid crop/layout, pre-cancelled capture, simulated write denial and low disk space, sheet allocation splitting |
| Inspection | Pixel difference and exact duplicate candidates; reviewed exclusions alter sheets while all original PNGs remain |

Additionally, the standard Python wheel built successfully with `pip wheel --no-deps --no-build-isolation .`, and `git diff --check` passed. Synthetic sample PNGs/ZIP were generated from the app's actual extraction/sheet/export pipeline. The offscreen UI screenshot in this folder is from the running application.

## Windows acceptance checklist — not executed here

1. Run `install_windows.ps1` on Windows 11; verify FFmpeg and ffprobe on PATH.
2. Open a Short in Brave. Select the window, refresh preview, and draw the crop. Check mixed-DPI displays and resizing. Try PrintWindow, and explicit foreground fallback if necessary. A protected/black surface is not evidence of a working capture.
3. Capture at 0.5 s, pause/resume, then stop; verify completed frames, elapsed labels, and automatic sheets. Close the selected window during a separate capture and verify preserved frames and the error message.
4. Run the local 30-second and detailed 10–12 s acceptance scenarios on Windows.
5. Reopen/rename/re-export sessions; review duplicates; verify deletion confirmation and files preserved on Cancel.
6. Install Playwright Chromium and attempt a normal accessible YouTube video. If consent/ads/restrictions prevent reliable seeking, verify the Browser Window Capture fallback. Do not bypass restrictions.
7. Build with `scripts/build_windows.ps1`; launch the executable on Windows with external FFmpeg available. Verify optional Chromium discovery, image export, and paths containing spaces/non-ASCII characters.

The supplied Windows GitHub Actions workflow has **not** run in this task. No Windows executable or signed installer has been produced. Windows Graphics Capture is not implemented; the initial native backend is PrintWindow plus client-DC fallback. See [limitations](limitations.md).

## Follow-up reliability and usability work

Five improvements were implemented: setup diagnostics/preflight tool checks, last-saved-frame preview during acquisition, video inspection and capture-size estimates, revealing the exported ZIP, and requiring a fresh browser preview plus explicit crop selection. Crop state is invalidated on source changes, including rejecting stale asynchronous preview results. The frame inspector now remains selected after native capture finishes.

The expanded suite covers real local-video inspection through the UI and a complete simulated-browser UI workflow: crop guard → preview/crop → capture → live preview → pause/resume → stop → automatic sheets → ZIP export and folder opening. The Win32 backend itself is still untested on this Linux host. The real Chromium integration continues to use local synthetic video rather than a live YouTube page.

The final follow-up command is the full `RUN_BROWSER_TESTS=1` command documented above. Its current count is 21 tests; the earlier 17-test report is the original baseline, not the expanded test count.

## Bug audit after Windows user feedback

The latest full run executed **25 tests, all passed, no skips** (11.489 seconds), including real Chromium capture of synthetic local footage and offscreen Qt workflows. Added regression coverage for per-frame output width with mixed image dimensions, excess grid columns, malformed session collections, corrupt library entries, invalid saved source selection, and cancellation restoring ready controls/status.

Fixes also reload browser windows when restoring Browser window mode, prevent late previews from replacing status during capture, pass cancellation through video inspection, and prevent a preview-image read failure from aborting frame acquisition. This does not establish native Win32/Brave or packaged executable readiness; the Windows checks above still apply.
