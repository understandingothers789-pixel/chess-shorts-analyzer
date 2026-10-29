# Third-party software

No paid service or API key is required. Dependencies retain their own licenses:

- Python: PSF license.
- Pillow: HPND license.
- PySide6 / Qt for Python: LGPLv3 / GPLv3 / commercial licensing options. This application uses the dynamically linked LGPL option. Keep Qt notices and shared libraries when distributing; review Qt's LGPL obligations, including replacement/relinking rights.
- Playwright: Apache-2.0. Chromium has its own third-party notices.
- PyInstaller: GPL with a bootloader exception permitting packaged applications.
- FFmpeg: external executable, not bundled. The particular build may be LGPL or GPL depending on its configuration. The project's synthetic fixtures were generated with an FFmpeg GPL build.

The build script does not create a signed installer or bundle FFmpeg or downloaded Chromium. Review all included dependency notices before redistributing a packaged application. No license for the application source has been chosen by its owner yet; third-party licenses do not license this repository's source.
