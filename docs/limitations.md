# Validation and remaining limits

This is an initial implementation, not a claim that every Windows/browser combination has been certified.

## Verified in the Linux development environment

- Python 3.12, FFmpeg 7.1, Pillow, PySide6 desktop UI using Qt's offscreen platform.
- A generated 30-second MP4 produces 60 chronological samples at 0.5 s, six default sheets, and a valid analysis ZIP with optional original images.
- Detailed extraction of 10–12 s at 0.1 s produces 20 samples.
- Real variable-frame-rate extraction selects existing source frames rather than manufacturing duplicates.
- Real Chromium seeks and screenshots a local HTML video through Playwright. An ad/error state is rejected.
- Desktop import → background extraction → asynchronous gallery → comparison/difference → ZIP export → saved-settings restore.
- Simulated window resize, pause/resume, monotonic timing, stop, and closure with preservation of completed frames.
- Corrupt/missing input, missing tools, cancellation, disk/write errors, unsafe metadata, sheet splitting, duplicate review semantics.

See `docs/VALIDATION.md` for the exact final test results.

## Requires Windows and user validation

- Native Brave/Chrome/Edge HWND enumeration, PrintWindow and foreground client-DC capture on an actual Windows 11 desktop, including mixed-DPI displays and GPU surfaces.
- Windows launcher, PowerShell installer, PyInstaller executable, and Windows CI have been supplied but have not been executed by this Linux task.
- Automated YouTube playback is best effort. No real YouTube session was tested here. Consent, ads, authentication, age restrictions, player changes, and platform policies may prevent capture. The app stops instead of bypassing them. Use native browser capture for these cases, subject to normal content protection.

## Capture behavior

- The native backend currently uses **PrintWindow**, with an explicitly selected, foreground-only **BitBlt client-DC fallback**. Windows Graphics Capture is not implemented in this version. These backends do not guarantee capture of minimized, obscured, GPU, or protected content. A black preview can be a capture limitation; the app does not attempt to defeat protection. Restore the browser, keep the video visible, and try fallback if needed.
- There is a three-second countdown before native acquisition. Click Start, then switch to the browser and play. The monotonic active clock begins after the countdown. Pause excludes paused time and does not pause Brave playback. End, when set, is an active-duration limit for live capture; start is not a media seek for this method.
- Crop coordinates are relative to the window client area, so resizing/display scaling is handled proportionally. A page reflow can move the video independently; refresh and reselect the crop after layout changes. Do not change windows, DPI layout, or browser tabs mid-capture without checking the result.
- Local extraction uses real presentation timestamps normalized to the first video frame. It chooses the first frame at/after each requested sample and omits repeated selections of the same source frame. Intervals faster than source frames do not invent extra frames. Range end is exclusive. Audio and color/HDR metadata are not exported; screenshots are ordinary RGB images.
- FFprobe scans frame timestamps and FFmpeg decodes once. Very long videos have a larger metadata scan and selection expression; this tool is optimized for Shorts and short research segments. Full-resolution images are processed one at a time, with sheet allocation capped at 60 million pixels by default.
- Browser automation timestamps are HTML video `currentTime` after seeking, not a guarantee of the exact compositor frame. Screenshots use the displayed video element's resolution, not the original stream resolution. A selected element can still include platform-rendered visuals.
- A stopped capture retains completed frames and generates sheets. An acquisition failure leaves recorded frames available for manual sheet generation. A process crash is recovered as an interrupted session when opened; fully registered originals are retained. Local extraction staging files from a hard crash are not automatically recovered.
- Duplicate detection is exact decoded-pixel comparison, disabled unless you invoke review. Only reviewed frames may be excluded from sheets; originals and exportable metadata are retained. Existing sheets must be regenerated to apply exclusions.
- Maximum-detail sheets put one frame per image with a label; original full-resolution files remain in `frames/`. Very large single images require choosing a smaller output frame width. The app splits oversized multi-frame sheets rather than silently shrinking originals.
- Session deletion is permanent after confirmation and includes nested detailed sessions. Session rename changes its display name, not the physical folder path.
- No chess engine, automatic move recognition, video editor, or animation recreation is included.
