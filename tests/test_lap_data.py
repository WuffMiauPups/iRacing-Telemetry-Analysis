"""Tests for the pure data helpers in telemetry.lap_data.

These run without matplotlib or PySide6 so they stay fast and deterministic.
"""

import os
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from telemetry.lap_data import (
    CHANNELS,
    RESAMPLE_POINTS,
    safe_float,
    load_and_group_laps,
    resample_lap,
    find_best_lap,
    get_lap_times_from_summary,
)


def _write_tick_csv(path, rows):
    header = [
        'Timestamp', 'Lap', 'LapDistPct', 'Speed_kmh',
        'Throttle', 'Brake', 'Gear', 'SteeringWheelAngle',
        'LapDeltaToBestLap',
    ]
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(','.join(header) + '\n')
        for r in rows:
            f.write(','.join(str(r.get(col, '')) for col in header) + '\n')


def _write_summary_csv(path, lap_time_by_lap):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('Lap,LapTime,FuelUsed\n')
        for lap, lt in lap_time_by_lap.items():
            f.write(f'{lap},{lt},0.5\n')


class TestSafeFloat(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(safe_float(None))

    def test_empty(self):
        self.assertIsNone(safe_float(''))

    def test_literal_none_string(self):
        self.assertIsNone(safe_float('None'))

    def test_good(self):
        self.assertEqual(safe_float('1.5'), 1.5)

    def test_bad(self):
        self.assertIsNone(safe_float('abc'))

    def test_default(self):
        self.assertEqual(safe_float(None, default=0), 0)


class TestLoadAndGroupLaps(unittest.TestCase):
    def test_groups_by_lap_and_drops_stationary(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 't.csv')
            rows = []
            for lap in (1, 2):
                for i in range(20):
                    rows.append({
                        'Lap': lap,
                        'LapDistPct': i / 20,
                        'Speed_kmh': 120 + i,
                        'Throttle': 0.8,
                        'Brake': 0.0,
                        'Gear': 4,
                        'SteeringWheelAngle': 0.1,
                        'LapDeltaToBestLap': 0.0,
                    })
            # Add a stationary row that must be dropped.
            rows.append({
                'Lap': 1, 'LapDistPct': 0.5, 'Speed_kmh': 0,
                'Throttle': 0, 'Brake': 1, 'Gear': 0,
                'SteeringWheelAngle': 0, 'LapDeltaToBestLap': 0,
            })
            _write_tick_csv(path, rows)

            laps = load_and_group_laps(path)
            self.assertEqual(set(laps.keys()), {1, 2})
            self.assertEqual(len(laps[1]), 20)
            self.assertEqual(len(laps[2]), 20)
            # Throttle/Brake should be scaled to 0-100.
            pct, values = laps[1][0]
            self.assertAlmostEqual(values['Throttle'], 80.0)
            self.assertAlmostEqual(values['Brake'], 0.0)
            # Sorted by pct within a lap.
            pcts = [p for p, _ in laps[1]]
            self.assertEqual(pcts, sorted(pcts))

    def test_skips_negative_pct_and_missing_lap(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 't.csv')
            rows = [
                {'Lap': 1, 'LapDistPct': 0.5, 'Speed_kmh': 100},
                {'Lap': '',  'LapDistPct': 0.6, 'Speed_kmh': 100},
                {'Lap': 1, 'LapDistPct': -0.1, 'Speed_kmh': 100},
            ]
            _write_tick_csv(path, rows)
            laps = load_and_group_laps(path)
            self.assertEqual(len(laps[1]), 1)


class TestResampleLap(unittest.TestCase):
    def test_returns_none_if_too_few_points(self):
        lap_data = [(0.1, {'Speed_kmh': 100})]
        p, v = resample_lap(lap_data, 'Speed_kmh')
        self.assertIsNone(p)
        self.assertIsNone(v)

    def test_resamples_to_requested_length(self):
        lap_data = [(i / 100, {'Speed_kmh': float(i)}) for i in range(50)]
        p, v = resample_lap(lap_data, 'Speed_kmh', num_points=200)
        self.assertEqual(len(p), 200)
        self.assertEqual(len(v), 200)
        # Output pcts are in 0-100 scale
        self.assertLess(p[0], 1.0)
        self.assertGreater(p[-1], 40.0)


class TestFindBestLap(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(find_best_lap({}))

    def test_picks_min(self):
        self.assertEqual(find_best_lap({1: 80.0, 2: 75.0, 3: 78.0}), 2)

    def test_ignores_zero_and_negative(self):
        self.assertEqual(find_best_lap({1: 0, 2: -1, 3: 80.0}), 3)


class TestGetLapTimesFromSummary(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(get_lap_times_from_summary(td), {})

    def test_reads_valid_rows(self):
        with tempfile.TemporaryDirectory() as td:
            _write_summary_csv(os.path.join(td, 'lap_summary.csv'),
                               {1: 80.5, 2: 75.2, 3: 0})
            times = get_lap_times_from_summary(td)
            # Keys are 0-based (tick-data convention) — summary Lap=1
            # corresponds to continuous_lap=0. Row with LapTime=0 filtered.
            self.assertEqual(times, {0: 80.5, 1: 75.2})


if __name__ == '__main__':
    unittest.main()
