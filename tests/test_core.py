import csv
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from chess_shorts.capture.local import extract, sampling_plan
from chess_shorts.export import export_chatgpt
from chess_shorts.session import Session
from chess_shorts.sheets import SheetConfig, generate


class CoreAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / 'test.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=180x320:rate=30:duration=30', '-c:v', 'libx264',
                        '-threads', '2', '-pix_fmt', 'yuv420p', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def session(self, interval=.5):
        return Session.create(self.root / 'sessions', 'local', interval, str(self.video))

    def test_a_and_d_overview_and_export(self):
        session = self.session()
        extract(self.video, session)
        frames = session.data['frames']
        self.assertEqual(len(frames), 60)
        self.assertEqual([f['video_timestamp_seconds'] for f in frames], [i * .5 for i in range(60)])
        self.assertTrue(all(f['elapsed_capture_seconds'] is None for f in frames))
        sheets = generate(session)
        self.assertEqual(len(sheets), 6)
        with Image.open(session.root / sheets[0]) as image:
            self.assertGreaterEqual(image.width, 420)
            self.assertGreater(image.height, 1600)
        result = export_chatgpt(session, include_frames=True)
        with zipfile.ZipFile(result) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('analysis_instructions.txt', archive.namelist())
            self.assertEqual(archive.getinfo('metadata.json').compress_type, zipfile.ZIP_DEFLATED)
            for member in archive.infolist():
                if member.filename.startswith(('frames/', 'contact_sheets/')):
                    self.assertEqual(member.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(archive.read(member), (session.root / member.filename).read_bytes())
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['screenshot_count'], 60)
            rows = list(csv.DictReader(io.StringIO(archive.read('timestamp_index.csv').decode())))
            self.assertEqual(len(rows), 60)
            self.assertEqual(float(rows[-1]['video_timestamp_seconds']), 29.5)
            self.assertEqual(len([p for p in archive.namelist() if p.startswith('frames/')]), 60)
        self.assertEqual(Session.load(session.root).data['screenshot_count'], 60)

    def test_c_detail(self):
        session = self.session(.1)
        extract(self.video, session, 10, 12)
        self.assertEqual(len(session.data['frames']), 20)
        self.assertAlmostEqual(session.data['frames'][-1]['video_timestamp_seconds'], 11.9)
        self.assertEqual(len(generate(session, SheetConfig(4, 2, 2))), 5)

    def test_variable_frame_rate_plan(self):
        times = [(0, 0), (1, .04), (2, .11), (3, .4), (4, .42), (5, .91)]
        plan = sampling_plan(times, .1, 0, 1)
        self.assertEqual([p[0] for p in plan], [0, 2, 3, 5])
        self.assertEqual([p[1] for p in plan], [0, .11, .4, .91])

    def test_invalid_video_and_missing_ffmpeg(self):
        with self.assertRaises(ValueError):
            extract(self.root / 'absent.mp4', self.session())
        with patch('chess_shorts.capture.local.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'ffprobe was not found'):
                extract(self.video, self.session())
        invalid = self.root / 'invalid.mp4'
        invalid.write_bytes(b'not a video')
        with self.assertRaisesRegex(RuntimeError, 'Cannot read video'):
            extract(invalid, self.session())

    def test_limits_split_without_resizing(self):
        session = self.session()
        for n in range(7):
            with Image.new('RGB', (200, 300), (n * 20, 20, 20)) as im:
                session.record(im, media=n * .5)
        sheets = generate(session, SheetConfig(max_dimension=800))
        self.assertEqual(len(sheets), 2)
        self.assertTrue((session.root / 'frames/frame_000001.png').exists())
        single = generate(session, SheetConfig(frames_per_sheet=10, columns=1, rows=1))
        self.assertEqual(len(single), 7)
        pairs = generate(session, SheetConfig(frames_per_sheet=10, columns=2, rows=1))
        self.assertEqual(len(pairs), 4)
        self.assertEqual(len(list((session.root / 'frames').glob('*.png'))), 7)

    def test_unsafe_metadata_and_unwritable_output(self):
        session = self.session()
        session.data['frames'] = [{'file': '../secret', 'number': 1}]
        session.save()
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            Session.load(session.root)
        with patch('chess_shorts.session.atomic_json', side_effect=PermissionError('read-only')):
            with self.assertRaises(PermissionError):
                self.session()


if __name__ == '__main__':
    unittest.main()


class ReliabilityTests(unittest.TestCase):
    def test_real_variable_frame_rate_extraction(self):
        from chess_shorts.capture.local import frame_times
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'variable.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=120x160:rate=30:duration=1', '-vf',
                            "select='not(mod(n,3))+eq(n,1)+eq(n,8)'", '-fps_mode', 'vfr',
                            '-c:v', 'libx264', '-threads', '2', str(video)], check=True)
            times = frame_times(video)
            self.assertGreater(len(set(round(b[1] - a[1], 3) for a, b in zip(times, times[1:]))), 1)
            session = Session.create(root / 'sessions', 'local', .05, str(video))
            extract(video, session, end=.9)
            actual = [f['video_timestamp_seconds'] for f in session.data['frames']]
            plan = sampling_plan(times, .05, 0, .9)
            self.assertEqual(actual, [stamp for _, stamp, _ in plan])
            self.assertEqual(len(actual), len(set(actual)))
            self.assertLess(len(actual), 18)
            self.assertTrue(all(f['video_timestamp_seconds'] + 1e-7 >= f['requested_timestamp_seconds'] for f in session.data['frames']))

    def test_cancel_preserves_a_reopenable_session(self):
        from chess_shorts.control import CaptureControl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'test.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=100x100:duration=1', '-c:v', 'libx264', '-threads', '2', str(video)], check=True)
            session = Session.create(root / 'sessions', 'local', .1, str(video))
            control = CaptureControl()
            control.stop()
            extract(video, session, control=control)
            self.assertEqual(Session.load(session.root).data['status'], 'stopped')

    def test_low_disk_and_reviewed_exclusion_keep_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session.create(root, 'window', .5, 'test')
            with Image.new('RGB', (100, 100)) as image:
                with patch('chess_shorts.session.shutil.disk_usage') as usage:
                    usage.return_value.free = 0
                    with self.assertRaisesRegex(OSError, 'disk space'):
                        session.record(image)
                for n in range(3):
                    session.record(image, elapsed=n * .5)
            session.data['frames'][1]['excluded_from_sheets'] = True
            session.save()
            generate(session, SheetConfig(1, 1, 1))
            self.assertEqual(len(session.data['contact_sheets']), 2)
            self.assertEqual(len(list((session.root / 'frames').glob('*.png'))), 3)
            self.assertEqual(Session.load(session.root).data['screenshot_count'], 3)
