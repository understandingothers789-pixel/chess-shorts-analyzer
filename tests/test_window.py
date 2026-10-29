import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from chess_shorts.capture.window import Window, capture_window, crop_image
from chess_shorts.control import CaptureControl
from chess_shorts.session import Session


class FakeWindowBackend:
    def __init__(self, close_after=None):
        self.calls = 0
        self.close_after = close_after

    def grab(self, *_args, **_kwargs):
        self.calls += 1
        if self.close_after and self.calls > self.close_after:
            raise RuntimeError('The selected browser window closed.')
        return Image.new('RGB', (200 + self.calls * 10, 400), 'navy')


class WindowTests(unittest.TestCase):
    def test_live_capture_resize_timing_pause_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'window', .02, 'Brave')
            control = CaptureControl()
            def progress(number, _total):
                if number == 2:
                    control.pause()
                    before = control.elapsed()
                    time.sleep(.03)
                    self.assertAlmostEqual(before, control.elapsed(), places=3)
                    control.resume()
                if number == 4:
                    control.stop()
            capture_window(FakeWindowBackend(), Window(1, 'Test', 1, 'brave.exe'),
                           (.25, .1, .75, .9), session, control, progress)
            frames = session.data['frames']
            self.assertEqual(len(frames), 4)
            self.assertEqual(session.data['status'], 'stopped')
            self.assertEqual([f['width'] for f in frames], [106, 110, 114, 120])
            self.assertTrue(all(f['video_timestamp_seconds'] is None for f in frames))
            self.assertEqual(sorted(f['elapsed_capture_seconds'] for f in frames),
                             [f['elapsed_capture_seconds'] for f in frames])

    def test_closed_window_preserves_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'window', .01, 'Brave')
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                capture_window(FakeWindowBackend(close_after=2), Window(1, 'Test', 1, 'brave.exe'),
                               (0, 0, 1, 1), session, CaptureControl())
            loaded = Session.load(session.root)
            self.assertEqual(loaded.data['status'], 'error')
            self.assertEqual(loaded.data['screenshot_count'], 2)

    def test_invalid_crop(self):
        with Image.new('RGB', (100, 100)) as image:
            with self.assertRaises(ValueError):
                crop_image(image, (1, 0, 0, 1))
