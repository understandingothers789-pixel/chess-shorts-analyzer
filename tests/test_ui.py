import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from chess_shorts.ui import Studio


@unittest.skipUnless(HAS_QT, 'PySide6 must be installed for desktop integration')
class DesktopIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertTrue(predicate(), 'Desktop operation timed out')

    def test_local_capture_inspect_compare_export_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'test.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=180x320:rate=20:duration=2', '-c:v', 'libx264',
                            '-threads', '2', str(video)], check=True)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            with patch('chess_shorts.ui.QSettings', return_value=settings), patch('chess_shorts.ui.QMessageBox.warning') as warning:
                studio = Studio()
                studio.output.setText(str(root / 'sessions'))
                studio.source.setText(str(video))
                studio.show()
                studio.inspect_video()
                self.wait_for(lambda: not studio.busy)
                self.assertIn('4 requested frames', studio.frame_estimate.text())
                studio.start_capture()
                self.assertTrue(studio.busy)
                self.wait_for(lambda: not studio.busy)
                self.assertIsNotNone(studio.session)
                self.assertEqual(studio.gallery.count(), 4)
                self.assertEqual(len(studio.session.data['contact_sheets']), 1)
                self.wait_for(lambda: not studio.jobs and not studio.thumbnail_jobs)
                self.assertIsNotNone(studio.viewer._item)
                studio.pin_frame()
                studio.navigate(1)
                studio.diff.setChecked(True)
                self.wait_for(lambda: not studio.jobs)
                self.assertIsNotNone(studio.compare_left._item)
                self.assertIsNotNone(studio.compare_right._item)
                studio.export_session()
                self.wait_for(lambda: not studio.busy)
                self.assertTrue((studio.session.root / 'contact_sheets.zip').exists())
                self.assertFalse(warning.called, warning.call_args_list)
                studio.grab().save(str(root / 'ui.png'))
                studio.close()
                self.app.processEvents()
                again = Studio()
                self.assertEqual(again.source.text(), str(video))
                self.assertEqual(again.output.text(), str(root / 'sessions'))
                self.assertEqual(again.interval.value(), .5)
                again.close()

    def test_window_capture_crop_preview_pause_stop_and_export(self):
        from PIL import Image
        from chess_shorts.capture.window import Window
        class FakeBackend:
            def windows(self):
                return [Window(1, 'Synthetic Brave', 123, 'brave.exe')]
            def grab(self, *_args, **_kwargs):
                return Image.new('RGB', (200, 400), 'navy')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            with patch('chess_shorts.ui.QSettings', return_value=settings), patch('chess_shorts.ui.WindowsCapture', FakeBackend), patch('chess_shorts.ui.QMessageBox.warning') as warning:
                studio = Studio()
                try:
                    studio.output.setText(str(root / 'sessions'))
                    studio.method.setCurrentIndex(1)
                    self.wait_for(lambda: studio.crop_ready)
                    self.assertFalse(studio.crop_selected)
                    studio.interval.setValue(.05)
                    studio.start_capture()
                    self.wait_for(lambda: studio.pending_session is not None and len(studio.pending_session.data['frames']) >= 3)
                    self.wait_for(lambda: studio.live_view._item is not None)
                    studio.toggle_pause()
                    self.assertTrue(studio.job.control.paused)
                    elapsed = studio.job.control.elapsed()
                    time.sleep(.12)
                    self.assertAlmostEqual(elapsed, studio.job.control.elapsed(), places=3)
                    studio.toggle_pause()
                    studio.stop_capture()
                    self.wait_for(lambda: not studio.busy)
                    self.assertEqual(studio.session.data['status'], 'stopped')
                    self.assertGreater(len(studio.session.data['contact_sheets']), 0)
                    self.assertEqual(studio.session.data['frames'][0]['width'], 200)
                    self.assertEqual(studio.tabs.currentIndex(), 1)
                    studio.export_session()
                    self.wait_for(lambda: not studio.busy)
                    self.assertTrue(studio.last_export.is_file())
                    self.assertTrue(studio.reveal_export_button.isEnabled())
                    with patch('chess_shorts.ui.QDesktopServices.openUrl', return_value=True) as opened:
                        # Test folder opening independently of OS Explorer integration.
                        studio.open_folder(studio.last_export.parent)
                        self.assertTrue(opened.called)
                    generation = studio.crop_generation
                    old_image = studio.crop_preview.image
                    studio.invalidate_crop()
                    studio.preview_ready((generation, old_image))
                    self.assertFalse(studio.crop_ready)
                    self.assertFalse(studio.crop_selected)
                    self.assertFalse(warning.called, warning.call_args_list)
                finally:
                    if studio.busy:
                        studio.stop_capture()
                        self.wait_for(lambda: not studio.busy)
                    studio.close()
