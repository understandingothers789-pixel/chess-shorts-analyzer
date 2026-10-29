import unittest
from chess_shorts.capture.youtube import normalize_url


class URLTests(unittest.TestCase):
    def test_normalization(self):
        expected = 'https://www.youtube.com/watch?v=abcdefgh_12'
        for url in ('https://youtube.com/shorts/abcdefgh_12?si=tracking',
                    'youtu.be/abcdefgh_12?t=4', 'https://m.youtube.com/watch?v=abcdefgh_12&feature=share'):
            self.assertEqual(normalize_url(url), expected)

    def test_invalid_urls(self):
        for url in ('javascript:alert(1)', 'https://youtube.com.evil.test/watch?v=abcdefgh_12',
                    'https://youtube.com/playlist?list=abc', 'https://youtu.be/short',
                    'https://user@youtube.com/watch?v=abcdefgh_12', 'https://youtube.com:123/shorts/abcdefgh_12'):
            with self.assertRaises(ValueError, msg=url):
                normalize_url(url)
