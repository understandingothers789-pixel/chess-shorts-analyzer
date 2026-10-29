import unittest
from unittest.mock import patch
from types import SimpleNamespace

from chess_shorts.readiness import diagnostics, estimate


class ReadinessTests(unittest.TestCase):
    def test_estimate_overview_detail_and_invalid_range(self):
        self.assertIn('60 requested frames', estimate(30, .5, 10, 1920, 1080))
        self.assertIn('6 sheets', estimate(30, .5, 10))
        self.assertIn('20 requested frames', estimate(2, .1, 4))
        self.assertIn('5 sheets', estimate(2, .1, 4))
        self.assertIn('119 MiB', estimate(30, .5, 10, 360, 960))
        for duration in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                estimate(duration, .5, 10)

    def test_missing_and_unusable_tools_reported(self):
        with patch('chess_shorts.readiness.executable', side_effect=RuntimeError('missing tool')):
            result = diagnostics()
        self.assertIn('ffmpeg: NOT READY', result)
        self.assertIn('ffprobe: NOT READY', result)
        with patch('chess_shorts.readiness.executable', return_value='ffmpeg'), patch('chess_shorts.readiness.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            self.assertIn('could not run the executable', diagnostics())
