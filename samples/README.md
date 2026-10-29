# Synthetic sample capture

`synthetic_test.mp4` is five seconds of an FFmpeg-generated 360 × 640 color/motion test pattern at 30 fps. It is not a chess clip or downloaded YouTube content.

`synthetic_session/` contains 10 full-resolution PNG frames, one default 2 × 5 contact sheet, actual media timestamps (0.0–4.5 seconds), metadata, and a verified analysis ZIP.

- [Contact sheet](synthetic_session/contact_sheets/sheet_001.png)
- [Metadata](synthetic_session/metadata.json)
- [ChatGPT export](synthetic_session/contact_sheets.zip)

Open `synthetic_session` using **Sessions → Open session folder** to explore the inspector and comparison tools. The saved source path describes the development machine where this sample was captured; generate a fresh session with `python scripts/generate_samples.py` to use your own local source path.
