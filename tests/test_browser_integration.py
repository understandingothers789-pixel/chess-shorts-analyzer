"""Opt-in real Chromium test; uses local generated footage, never YouTube."""
import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from chess_shorts.capture.youtube import AutomationUnavailable, capture_page
from chess_shorts.control import CaptureControl
from chess_shorts.session import Session


class MediaHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Browsers require byte ranges for reliable seeking.
        if self.path == '/test.webm':
            data = (Path(self.directory) / 'test.webm').read_bytes()
            start, end = 0, len(data) - 1
            requested = self.headers.get('Range')
            if requested:
                bounds = requested.removeprefix('bytes=').split('-', 1)
                start = int(bounds[0] or 0)
                end = min(int(bounds[1]) if bounds[1] else end, end)
            self.send_response(206 if requested else 200)
            self.send_header('Content-Type', 'video/webm')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if requested:
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
            self.end_headers()
            self.wfile.write(data[start:end + 1])
        else:
            super().do_GET()


@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', 'Set RUN_BROWSER_TESTS=1 to run real Chromium integration')
class BrowserIntegration(unittest.TestCase):
    def test_seeking_and_unavailable_video(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=180x320:rate=20:duration=2', '-c:v', 'libvpx-vp9',
                            '-threads', '2', '-an', str(root / 'test.webm')], check=True)
            (root / 'index.html').write_text('<title>Test footage</title><video src="test.webm" controls preload="auto" style="width:180px;height:320px"></video>')
            handler = functools.partial(MediaHandler, directory=directory)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as p:
                    kwargs = {'headless': True}
                    if os.environ.get('TEST_CHROMIUM_EXECUTABLE'):
                        kwargs['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
                    browser = p.chromium.launch(**kwargs)
                    try:
                        page = browser.new_page()
                        page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                        session = Session.create(root / 'sessions', 'youtube', .5, 'local browser test')
                        capture_page(page, session, CaptureControl(), end=2)
                        self.assertEqual(len(session.data['frames']), 4)
                        self.assertEqual([f['video_timestamp_seconds'] for f in session.data['frames']], [0, .5, 1, 1.5])
                        self.assertEqual(session.data['video_title'], 'Test footage')
                        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div class=ad-showing></div>')")
                        failed = Session.create(root / 'sessions', 'youtube', .5, 'local error test')
                        with self.assertRaisesRegex(AutomationUnavailable, 'advertisement'):
                            capture_page(page, failed, CaptureControl(), end=1)
                        self.assertEqual(failed.data['status'], 'error')
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
