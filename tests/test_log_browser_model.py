"""Tests for the folder-name parser in gui.log_browser_model.

Kept as a pure-function test (no Qt widget construction) so it runs in
environments without a display server.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui.log_browser_model import parse_folder_name


class TestParseFolderName(unittest.TestCase):
    def test_simple_practice(self):
        info = parse_folder_name('2026-04-16_20-48_Hockenheimring_Practice')
        self.assertIsNotNone(info)
        self.assertEqual(info['date'], '2026-04-16')
        self.assertEqual(info['time'], '20:48')
        self.assertEqual(info['track'], 'Hockenheimring')
        self.assertEqual(info['session_type'], 'Practice')

    def test_multi_word_track(self):
        info = parse_folder_name('2026-04-04_21-40_Motorsport_Arena_Oschersleben_Race')
        self.assertEqual(info['track'], 'Motorsport Arena Oschersleben')
        self.assertEqual(info['session_type'], 'Race')

    def test_underscored_session_suffix(self):
        # Lone_Qualify must win over Qualify so the track is left intact.
        info = parse_folder_name('2026-04-16_20-50_Hockenheimring_Baden-Württemberg_Lone_Qualify')
        self.assertEqual(info['track'], 'Hockenheimring Baden-Württemberg')
        self.assertEqual(info['session_type'], 'Lone Qualify')

    def test_offline_testing(self):
        info = parse_folder_name('2026-03-25_20-27_Donington_Park_Racing_Circuit_Offline_Testing')
        self.assertEqual(info['track'], 'Donington Park Racing Circuit')
        self.assertEqual(info['session_type'], 'Offline Testing')

    def test_non_matching_returns_none(self):
        self.assertIsNone(parse_folder_name('something-else'))
        self.assertIsNone(parse_folder_name('foo_bar_baz'))

    def test_track_with_dash(self):
        info = parse_folder_name('2026-04-08_13-25_Winton_Motor_Raceway_-_National_Practice')
        self.assertEqual(info['track'], 'Winton Motor Raceway - National')
        self.assertEqual(info['session_type'], 'Practice')


if __name__ == '__main__':
    unittest.main()
