import tempfile
import unittest
from pathlib import Path
from PIL import Image
from chess_shorts.session import Session
from chess_shorts.sheets import SheetConfig, generate


class RegressionTests(unittest.TestCase):
    def test_frame_width_not_expanded_to_label_width(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'local', .5, 'test')
            with Image.new('RGB', (400, 400), 'red') as image:
                session.record(image, media=0)
            with Image.new('RGB', (40, 800), 'blue') as image:
                session.record(image, media=.5)
            sheets = generate(session, SheetConfig(frame_width=100))
            with Image.open(session.root / sheets[0]) as image:
                reds = sum(image.getpixel((x, 400)) == (255, 0, 0) for x in range(image.width))
                self.assertEqual(reds, 100)

    def test_unused_columns_do_not_make_oversized_sheets(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'local', .5, 'test')
            with Image.new('RGB', (100, 100)) as image:
                session.record(image)
                session.record(image)
            a = generate(session, SheetConfig(1, 1, 1))
            with Image.open(session.root / a[0]) as image:
                expected = image.size
            b = generate(session, SheetConfig(1, 20, 1))
            with Image.open(session.root / b[0]) as image:
                self.assertEqual(image.size, expected)

    def test_malformed_session_collections_raise_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.create(Path(directory), 'local', .5, 'test')
            session.data['contact_sheets'] = None
            session.save()
            with self.assertRaisesRegex(ValueError, 'contact sheet metadata'):
                Session.load(session.root)
