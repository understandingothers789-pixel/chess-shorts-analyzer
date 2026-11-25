import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from chess_shorts.capture.local import extract
from chess_shorts.compact_export import ExportConfig, export_compact
from chess_shorts.export import export_chatgpt
from chess_shorts.session import Session
from chess_shorts.sheets import generate


class IndexedCaptureTests(unittest.TestCase):
    def test_hundredth_second_sampling_matches_original_pixels_and_saves_no_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / '100fps.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x96:rate=100:duration=1.1', '-c:v', 'libx264',
                            '-threads', '2', str(video)], check=True)
            source = Session.create(root, 'local', .01, str(video), storage='source')
            png = Session.create(root, 'local', .01, str(video))
            extract(video, source, .1, .6)
            extract(video, png, .1, .6)
            source = Session.load(source.root)
            self.assertEqual(len(source.data['frames']), 50)
            self.assertEqual(list((source.root / 'frames').iterdir()), [])
            self.assertEqual(
                [f['video_timestamp_seconds'] for f in source.data['frames']],
                [f['video_timestamp_seconds'] for f in png.data['frames']],
            )
            for index in (0, 22, 49):
                with source.open_frame(source.data['frames'][index]) as actual, png.open_frame(png.data['frames'][index]) as saved:
                    self.assertEqual(actual.size, saved.size)
                    self.assertEqual(actual.tobytes(), saved.tobytes())
            source.data['picked_frames'] = [23, 50]
            source.save()
            generated = generate(source, picked_only=True)
            self.assertEqual(len(generated), 1)
            result = export_compact(source, ExportConfig(range_kind='picked'))
            manifest = json.loads((result.folder / 'manifest.json').read_text())
            self.assertEqual([entry['number'] for entry in manifest['frames']], [23, 50])
            self.assertEqual([entry['source_frame_index'] for entry in manifest['frames']], [32, 59])
            self.assertEqual(list((source.root / 'frames').iterdir()), [])
            archive = export_chatgpt(source, include_frames=True)
            restored = root / 'restored'
            with ZipFile(archive) as data:
                data.extractall(restored)
            video.unlink()
            reloaded = Session.load(restored)
            with reloaded.open_frame(reloaded.data['frames'][22]) as frame:
                self.assertEqual(frame.size, (160, 96))
            with self.assertRaisesRegex(FileNotFoundError, "Original video is missing"):
                source.open_frame(source.data['frames'][22])

    def test_compact_screen_capture_keeps_sample_times_with_one_shared_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'window', .01, 'Brave', storage='jpeg')
            with Image.new('RGB', (288, 512), 'navy') as frame:
                for i in range(20):
                    session.record(frame, elapsed=i * .01)
            session.finish()
            session = Session.load(session.root)
            self.assertEqual(len(session.data['frames']), 20)
            self.assertEqual(len(list((session.root / 'frames').iterdir())), 1)
            self.assertEqual(session.data['frames'][-1]['elapsed_capture_seconds'], .19)
            self.assertEqual(len({frame['file'] for frame in session.data['frames']}), 1)
