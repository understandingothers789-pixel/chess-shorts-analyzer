import hashlib
import json
import random
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from chess_shorts.compact_export import ExportConfig, export_compact
from chess_shorts.control import Cancelled, CaptureControl
from chess_shorts.session import Session
from chess_shorts.sheets import font


class CompactExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Path(temporary.name)
        self.random = random.Random(7)

    def add_frame(self, session, *, media=None, elapsed=None, requested=None, size=(640, 480), noise=True):
        if noise:
            image = Image.frombytes('RGB', size, self.random.randbytes(size[0] * size[1] * 3))
        else:
            image = Image.new('RGB', size, 'navy')
        with image:
            session.record(image, media=media, elapsed=elapsed, requested=requested,
                           captured_at='2026-05-01T10:00:00+00:00')

    def manifest(self, result):
        return json.loads((result.folder / 'manifest.json').read_text(encoding='utf-8'))

    def check_batches(self, result, manifest, limit):
        self.assertGreater(len(manifest['batches']), 1)
        ordered_numbers = []
        for index, batch in enumerate(manifest['batches'], 1):
            self.assertEqual(batch['folder'], f'batch_{index:03d}')
            actual_files = sorted((result.folder / batch['folder']).iterdir())
            self.assertEqual(len(actual_files), batch['file_count'])
            self.assertEqual([path.relative_to(result.folder).as_posix() for path in actual_files],
                             [image['file'] for image in batch['images']])
            actual_bytes = sum(path.stat().st_size for path in actual_files)
            self.assertEqual(actual_bytes, batch['image_bytes'])
            self.assertLessEqual(actual_bytes, limit)
            for path, image in zip(actual_files, batch['images']):
                self.assertEqual(path.stat().st_size, image['bytes'])
                with Image.open(path) as decoded:
                    self.assertEqual(decoded.format, 'JPEG')
                    self.assertEqual(decoded.size, (image['width'], image['height']))
                    decoded.verify()
                ordered_numbers.extend(image['frame_numbers'])
        return ordered_numbers

    def test_jpeg_batches_preserve_all_selected_frames_exact_times_and_originals(self):
        session = Session.create(self.library, 'local', .1, 'chess.mp4')
        session.data['sampling_policy'] = 'first source frame at or after each target'
        for index, time in enumerate([.3, .1, .2, .2, .5, .4]):
            self.add_frame(session, media=time, requested=index / 10)
        session.data['frames'][2]['excluded_from_sheets'] = True
        session.finish()
        originals = [session.root / frame['file'] for frame in session.data['frames']]
        originals.append(session.root / 'metadata.json')
        checksums = [hashlib.sha256(path.read_bytes()).digest() for path in originals]

        limit = 1_400_000
        result = export_compact(session, ExportConfig(batch_bytes=limit, zip_batches=True,
                                                      range_kind='time', start=.1, end=.4))
        meta = self.manifest(result)
        self.assertEqual(self.check_batches(result, meta, limit), [2, 3, 4, 1, 6])
        self.assertTrue(any(batch['file_count'] > 1 for batch in result.batches))
        self.assertEqual([frame['number'] for frame in meta['frames']], [2, 3, 4, 1, 6])
        self.assertEqual([frame['video_timestamp_seconds'] for frame in meta['frames']], [.1, .2, .2, .3, .4])
        self.assertEqual([frame['requested_timestamp_seconds'] for frame in meta['frames']], [.1, .2, .3, 0, .5])
        self.assertTrue(all(frame['elapsed_capture_seconds'] is None for frame in meta['frames']))
        self.assertTrue(all(frame['captured_at'] == '2026-05-01T10:00:00+00:00' for frame in meta['frames']))
        self.assertEqual(meta['source_session']['timing_basis'], 'media')
        self.assertEqual(meta['source_session']['requested_interval_seconds'], .1)
        self.assertEqual(meta['source_session']['sampling_policy'], session.data['sampling_policy'])
        self.assertEqual(meta['exported_frame_count'], 5)
        self.assertEqual(checksums, [hashlib.sha256(path.read_bytes()).digest() for path in originals])
        self.assertTrue((result.folder / 'UPLOAD_INSTRUCTIONS.txt').is_file())
        for batch in meta['batches']:
            with zipfile.ZipFile(result.folder / batch['zip_file']) as archive:
                self.assertEqual(archive.namelist(), [Path(image['file']).name for image in batch['images']])
                for image in batch['images']:
                    path = result.folder / image['file']
                    self.assertEqual(archive.read(path.name), path.read_bytes())

    def test_missing_and_unreadable_files_report_original_numbers_and_elapsed_times(self):
        session = Session.create(self.library, 'window', .1, 'Synthetic Brave')
        session.data.update(timing_note='Active screen capture only', capture_backend='PrintWindow client',
                            interruptions=[{'type': 'pause', 'at': '2026-05-01T10:00:00+00:00'}])
        for seconds in [.1, .2, .3, .4, None]:
            self.add_frame(session, elapsed=seconds, noise=False)
        session.finish()
        missing = session.root / session.data['frames'][1]['file']
        missing.unlink()
        corrupt = session.root / session.data['frames'][2]['file']
        corrupt.write_bytes(b'Not a valid image')
        stored = (session.root / 'metadata.json').read_bytes()

        result = export_compact(Session.load(session.root), ExportConfig(range_kind='time', start=.1, end=.4))
        meta = self.manifest(result)
        self.assertEqual(result.exported_frames, 2)
        self.assertEqual(meta['time_range_field'], 'elapsed_capture_seconds')
        self.assertEqual(meta['source_session']['timing_basis'], 'elapsed_capture')
        self.assertEqual(meta['source_session']['capture_backend'], 'PrintWindow client')
        self.assertEqual(meta['source_session']['timing_note'], 'Active screen capture only')
        self.assertEqual(meta['source_session']['interruptions'], session.data['interruptions'])
        self.assertEqual(meta['skipped_frame_numbers'], [2, 3])
        self.assertEqual([frame['elapsed_capture_seconds'] for frame in meta['frames']], [.1, .2, .3, .4])
        self.assertTrue(all(frame['video_timestamp_seconds'] is None for frame in meta['frames']))
        self.assertTrue(meta['frames'][1]['skip_reason'].endswith('missing'))
        self.assertTrue(meta['frames'][2]['skip_reason'].endswith('read'))
        self.assertEqual(stored, (session.root / 'metadata.json').read_bytes())

        all_missing = export_compact(session, ExportConfig(range_kind='frame', start=2, end=3))
        self.assertEqual(all_missing.exported_frames, 0)
        self.assertEqual(all_missing.batches, [])
        self.assertEqual(self.manifest(all_missing)['skipped_frame_numbers'], [2, 3])

        unknown = export_compact(session, ExportConfig(range_kind='frame', start=5, end=5))
        self.assertIsNone(self.manifest(unknown)['frames'][0]['elapsed_capture_seconds'])

    def test_contact_sheets_split_to_preserve_detail_and_include_readable_labels(self):
        session = Session.create(self.library, 'window', .1, 'Synthetic Brave')
        for index in range(8):
            self.add_frame(session, elapsed=index / 10)
        session.finish()
        limit = 1_400_000
        result = export_compact(session, ExportConfig(layout='sheets', frames_per_sheet=4, batch_bytes=limit))
        meta = self.manifest(result)
        self.assertEqual(self.check_batches(result, meta, limit), list(range(1, 9)))
        sheets = [image for batch in meta['batches'] for image in batch['images']]
        self.assertEqual(len(sheets), 4)
        self.assertTrue(all(image['frame_numbers'] and len(image['frame_numbers']) == 2 for image in sheets))
        self.assertEqual([entry['position_in_image'] for entry in meta['frames']], [1, 2] * 4)
        for sheet in sheets:
            self.assertEqual(sheet['jpeg_quality'], 86)
            with Image.open(result.folder / sheet['file']) as image:
                self.assertEqual(image.size, (1316, 540))
                for index, frame in enumerate(sheet['frame_numbers']):
                    x = 12 + index * (640 + 12)
                    with image.crop((x, 492, x + 640, 528)).convert('L') as label:
                        with Image.new('L', label.size) as expected:
                            line = f'#{frame:06d}  00:00.{(frame - 1) * 100:03d} elapsed'
                            ImageDraw.Draw(expected).text((6, 5), line, fill=255, font=font(24))
                            expected_mask = expected.point(lambda px: 255 if px >= 140 else 0)
                            actual_mask = label.point(lambda px: 255 if px >= 140 else 0)
                            overlap = ImageChops.multiply(actual_mask, expected_mask).histogram()[255]
                            self.assertGreater(overlap, expected_mask.histogram()[255] * .90)
                            self.assertGreater(overlap, actual_mask.histogram()[255] * .90)

    def test_oversized_individual_frame_reduces_quality_and_size(self):
        session = Session.create(self.library, 'local', .1, 'chess.mp4')
        self.add_frame(session, media=.1, size=(960, 720))
        session.finish()

        limit = 180_000
        result = export_compact(session, ExportConfig(batch_bytes=limit))
        image = result.batches[0]['images'][0]
        self.assertLessEqual((result.folder / image['file']).stat().st_size, limit)
        self.assertGreaterEqual(image['width'], 480)
        self.assertLess(image['width'], 960)
        self.assertGreaterEqual(image['jpeg_quality'], 70)

    def test_cancel_and_impossible_budget_leave_completed_export_in_place(self):
        session = Session.create(self.library, 'local', .1, 'chess.mp4')
        self.add_frame(session, media=0)
        self.add_frame(session, media=.1)
        session.finish()
        previous = export_compact(session)
        control = CaptureControl()
        with self.assertLogs('chess_shorts.compact_export', level='INFO') as logs:
            with self.assertRaises(Cancelled):
                export_compact(session, control=control, progress=lambda *_: control.stop())
        self.assertIn('ChatGPT export started', logs.output[0])
        self.assertIn('ChatGPT export cancelled', logs.output[-1])
        with self.assertLogs('chess_shorts.compact_export', level='ERROR'):
            with self.assertRaisesRegex(ValueError, 'Increase the batch budget'):
                export_compact(session, ExportConfig(batch_bytes=1))
        self.assertEqual(list(previous.folder.parent.iterdir()), [previous.folder])
        self.assertEqual(self.manifest(previous)['exported_frame_count'], 2)
