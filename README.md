# Chess Shorts · Visual Analysis Studio

A local-first Windows 11 desktop app for capturing and inspecting video frames, generating timestamped contact sheets, and exporting visual research for ChatGPT. No API keys, subscriptions, chess engine, or video editor.

- **[Download version 0.3.1 for Windows x64 (.zip)](https://github.com/understandingothers789-pixel/chess-shorts-analyzer/releases/download/v0.3.1/ChessShortsStudio-v0.3.1-windows-x64.zip)**
- **[Download version 0.2 for Windows x64 (.zip)](https://github.com/understandingothers789-pixel/chess-shorts-analyzer/releases/download/v0.2.0/ChessShortsStudio-v0.2.0-windows-x64.zip)**
- **[Download version 0.1 for Windows x64 (.zip)](https://github.com/understandingothers789-pixel/chess-shorts-analyzer/releases/download/v0.1.0/ChessShortsStudio-v0.1.0-windows-x64.zip)**

Extract the ZIP, open the `ChessShortsStudio` folder, and run `ChessShortsStudio.exe`.

![Running desktop application](docs/studio-screenshot.png)

## Install on Windows 11

1. Install **64-bit Python 3.12+** from [python.org](https://www.python.org/downloads/), including the Python launcher.
2. Download and extract this repository, then double-click **install_windows.bat**. Wait for “Setup complete”. It installs the app into its own folder and does not change PowerShell settings.
3. Double-click **launch_windows.bat** to open the app.
4. To import local videos, install **FFmpeg and ffprobe** and make both available on PATH. One option is `winget install --id Gyan.FFmpeg --exact` in Command Prompt. Reopen the app after installation. You can use **Check setup** in the app to see whether they are available.

For optional YouTube URL automation, open Command Prompt in the app folder and run `.venv\Scripts\python.exe -m playwright install chromium`. Brave window capture and local import do not require Chromium. If your computer is managed by an organization, its security rules may restrict installers or downloads; follow your administrator’s guidance.

## Everyday Brave workflow

1. Open a Short in Brave, Chrome, or Edge. Keep the browser restored and the video visible.
2. In the app, select **Browser window**, select your browser window. The preview loads automatically; use **Refresh browser windows** if needed.
3. Optionally drag a rectangle around the video in the preview. Without a crop the app captures the whole browser window.
4. Choose an interval (default **0.5 seconds**). Click **Start Capture**. During the three-second countdown, switch to Brave and play the video.
5. Click **Stop** when finished. Completed frames are preserved. Use **Generate Contact Sheets** to make sheets. Pause/Resume controls collection while browser playback continues.
6. Click **Export for ChatGPT**, choose individual JPEGs or contact sheets, then click **Create ChatGPT export**. Use **Open export folder** to find the numbered batches.

Native window capture currently uses PrintWindow, with a foreground-only fallback for compatible surfaces. It does not promise minimized, obscured, protected, or every GPU window. Check the preview; see [limitations](docs/limitations.md). Actual elapsed capture times are labeled separately from media timestamps.

## Local videos and detailed animation capture

Choose **Local video**, select MP4/WebM/MOV/MKV, and set the interval. **Low storage · automatic** stores a timestamp index linked to your original video. The inspector, comparison, and exports decode frames only when requested. Keep the original video available. If it moves, use **Relink original video** and select the unchanged file. Choose **Original PNG files** if you want a self-contained set of images at capture time.

For **Detailed animation capture**, set a start and end, then choose the **0.01 seconds** preset. The end is exclusive. Each request uses the first real source frame at or after that time, with repeat selections omitted. A 30 FPS clip still has about 30 unique frames per second. A 100 FPS source can provide a new frame at every 0.01 seconds. Browser-window capture depends on the screen and capture speed.

Browser and YouTube captures use compact JPEGs in low-storage mode. Identical consecutive screenshots share one stored file and retain their individual timestamps. Contact sheets are generated on demand unless you enable **Generate sheets after capture**. If a session is open during a detailed capture, the new capture is saved in its `details/` folder.

## Inspect, organize, and export

- **Frames** loads thumbnails near the visible rows. Scrub the frame slider, enter seconds and choose **Go to time**, or step with arrow keys. The inspector shows the actual captured timestamp.
- Check individual frames in the gallery or press **Ctrl+Space** to toggle the current frame. Check the first frame and use **Pick through current** to mark an inclusive span. Picks survive reopening. **Export picked frames** sends only those frames to the export panel. **Export current frame** prepares a single frame without changing your picks.
- **Compare A / B** lets you pin a frame as A and select another as B. Difference shows absolute RGB difference at 2× contrast; equal dimensions are required.
- **Review exact duplicates** scans on request. Check individual duplicates to exclude them from future sheets; originals are never deleted. Regenerate sheets afterward.
- **Contact sheets** uses picked frames when any are checked. Clear picks to include all frames eligible for sheets. It offers 10, 4, or 1 frame presets and custom rows/columns, spacing, background, labels, PNG/JPEG, and output frame width. Zero width preserves original pixels. Oversized sheets split automatically. PNG is recommended for detailed analysis.
- **Sessions** reopens, renames, deletes after confirmation, regenerates, and re-exports local sessions. Double-click a session or open a folder containing `metadata.json`. Settings persist between launches.
- **YouTube URL** normalizes Shorts/watch/share URLs and attempts Playwright-controlled seeking in a separate Chromium profile. It does not use your Brave login or bypass restrictions. If automation fails, select Browser window and capture the video normally.
- **Export for ChatGPT** creates a compact copy of the open session. Choose picked frames, all captures, or an inclusive time or frame-number range. Time ranges use video timestamps for local videos and YouTube URL captures, and active elapsed capture time for browser window captures.
- **Apply compact animation preset** sets JPEG contact sheets to four frames per sheet, quality 75, and 640 pixels per frame. Choose your frame range or picks first. You can still fine-tune every setting after applying the preset.
- Choose **Individual JPEG frames** or **Contact sheets (JPEG)** with up to six labeled frames per sheet. JPEG quality and maximum frame size are adjustable. The default batch budget is **2.5 MiB** total JPEG size on disk. The app starts another batch when needed and may split a sheet or reduce a single image’s quality or size to fit. If an image cannot fit at the minimum export detail, increase the budget and retry. JPEG export leaves the session and its original media intact.
- Open the export folder and attach the JPEGs from `batch_001` in filename order, then continue with the other batches in order. The batch budget is an export target. ChatGPT applies its own message and attachment limits, so send smaller groups if needed. `manifest.json` records exact timestamps, capture details, and any missing or unreadable frames. Optional batch ZIPs are saved alongside the batches, and nothing is uploaded by the app.
- **Create full-quality ZIP** in the ChatGPT export panel creates the original `contact_sheets.zip` with the existing sheets, metadata, CSV timestamp index, and analysis instructions. **Include original frames in ZIP** adds the original captures. For indexed videos it decodes the frames into the ZIP, making the extracted session self-contained.

Default library: `Documents/ChessShortsStudio` (change it under Local storage).

```text
ChessShort_YYYY-MM-DD_HHMMSS_unique/
  frames/frame_000001.png
  contact_sheets/sheet_001.png
  details/                         # nested detailed sessions
  metadata.json
  contact_sheets.zip               # after Create full-quality ZIP
  chatgpt_exports/
    export_YYYYMMDD_HHMMSS_unique/
      manifest.json
      UPLOAD_INSTRUCTIONS.txt
      batch_001/                   # JPEGs to attach directly
      batch_002/
      archives/                    # optional batch ZIPs
```

Metadata includes source/title when known, UTC capture date, method, requested interval, actual media or elapsed timestamps, dimensions, sampling policy, sheet configuration, and interruptions/errors. Unknown fields stay null. A live window capture does not know the video's URL or playback timestamp.

## Development and tests

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux/macOS use `.venv/bin/python`; local extraction, export, and Qt UI work there, but native window capture requires Windows. For headless UI tests set `QT_QPA_PLATFORM=offscreen`. Tests use `unittest` and generate their own synthetic footage with FFmpeg; they never need private or copyrighted clips.

For real local-video browser integration, install Chromium and set `RUN_BROWSER_TESTS=1` before running tests. `TEST_CHROMIUM_EXECUTABLE` may point the test harness to a supported system Chromium. This test uses a loopback HTTP server with byte-range support and does not contact YouTube.

Generate fresh sample footage, contact sheets, metadata, and a ZIP:

```powershell
.\.venv\Scripts\python.exe scripts/generate_samples.py
```

See [samples](samples/README.md), [validation](docs/VALIDATION.md), [architecture](docs/architecture.md), and the [capture and selection changes](docs/capture-workflow.md).

## Windows executable

Run `scripts/build_windows.ps1` on Windows after installation. Distribute the entire `dist/ChessShortsStudio` directory; the entry point is `ChessShortsStudio.exe`. FFmpeg remains an external prerequisite, and optional Playwright Chromium is installed in the user's normal browser cache. There is no signed installer yet.

The Windows GitHub Actions workflow runs core, browser, and offscreen desktop tests and checks that the packaged executable starts. Builds triggered by version tags publish a Windows ZIP to GitHub Releases. Review [third-party licenses](docs/THIRD_PARTY.md) before distributing a bundle.

## First-run checks and capture helpers

- **Check setup** reports FFmpeg/ffprobe availability and whether optional Chromium is listed in Playwright's browser cache. Native window capture is Windows-only. Packaged builds refer you to the browser installation instructions instead of trying to launch a Python CLI.
- **Inspect video / estimate** reads the selected local video's duration and dimensions. The estimate shows requested time slots and average source frame rate when available. Original PNG mode also estimates uncompressed image size. Before inspecting, a clearly labeled 30-second example is shown.
- **Last captured frame** displays a reduced preview of the latest saved image while capture runs (updated at most twice per second). It is a visual check, not a real-time playback monitor.
- Browser capture previews the selected window automatically. Drag a crop if you want only part of the window; otherwise it captures the entire window. Changing windows clears the crop. A delayed preview from an older selection is discarded.
- **Show exported ZIP** reveals the full-quality ZIP in Windows Explorer, or opens its containing folder on other platforms. It also works when reopening a session with an existing full-quality ZIP.

Contact sheet grids adjust automatically: if the requested count exceeds the grid, extra frames go onto subsequent sheets in chronological order. The **Two frames · side by side** preset gives larger images for inspection. Originals remain unchanged.
