import tempfile
import unittest
from pathlib import Path
from PIL import Image
from chess_shorts.inspection import difference, find_duplicates
from chess_shorts.session import Session


class InspectionTests(unittest.TestCase):
    def test_difference_duplicates_and_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'local', .5, 'test')
            for color in ('red', 'blue', 'red'):
                with Image.new('RGB', (30, 40), color) as image:
                    session.record(image, media=len(session.data['frames']) * .5)
            self.assertEqual(find_duplicates(session), [(3, 1)])
            paths = [session.root / f['file'] for f in session.data['frames']]
            with difference(paths[0], paths[1]) as image:
                self.assertIsNotNone(image.getbbox())
            with difference(paths[0], paths[2]) as image:
                self.assertIsNone(image.getbbox())
            self.assertTrue(all(path.exists() for path in paths))

    def test_duplicate_hash_across_chunks_modes_and_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'local', .5, 'test')
            # Pixel-equivalent files can have different PNG modes/encodings.
            for mode, size, color in [('RGB', (30, 600), (0, 0, 0)),
                                      ('L', (30, 600), 0),
                                      ('RGB', (60, 300), (0, 0, 0))]:
                with Image.new(mode, size, color) as image:
                    session.record(image)
            with Image.new('RGB', (30, 600)) as image:
                image.putpixel((5, 599), (1, 0, 0))
                session.record(image)
            self.assertEqual(find_duplicates(session), [(2, 1)])
