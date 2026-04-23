"""Pure-function unit tests. No iRacing dependency.

Run with:  python -m pytest tests/
Or:        python -m unittest discover -s tests
"""

import math
import os
import sys
import time
import unittest

# Make project root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from telemetry.timing import (
    SectorTracker, CatchCalculator, _safe_idx, DEFAULT_SECTOR_SPLITS,
)
from telemetry.track_map import TrackMapper
from telemetry.track_db import _sanitize_filename
from telemetry.session import SessionMonitor
from telemetry.pit_window import (
    estimate_laps_remaining, fuel_per_lap_from_history, fuel_for_laps,
    compute_pit_window,
)


class TestSafeIdx(unittest.TestCase):
    def test_none_array(self):
        self.assertIsNone(_safe_idx(None, 0))

    def test_in_range(self):
        self.assertEqual(_safe_idx([1, 2, 3], 1), 2)

    def test_out_of_range(self):
        self.assertIsNone(_safe_idx([1], 5))

    def test_wrong_type(self):
        self.assertIsNone(_safe_idx(42, 0))


class TestSectorTracker(unittest.TestCase):
    def test_default_three_sectors(self):
        st = SectorTracker()
        self.assertEqual(st.num_sectors, 3)

    def test_sector_index_lookup(self):
        st = SectorTracker()
        self.assertEqual(st._get_sector_index(0.10), 0)
        self.assertEqual(st._get_sector_index(0.50), 1)
        self.assertEqual(st._get_sector_index(0.90), 2)

    def test_unknown_car_returns_empty(self):
        st = SectorTracker()
        self.assertEqual(st.get_last_lap_sectors(99), [])
        self.assertEqual(st.get_current_sectors(99), [])

    def test_full_lap_completion(self):
        st = SectorTracker()
        # Drive through three sectors then cross start/finish
        for pct in [0.10, 0.40, 0.80, 0.05]:
            st.update(0, pct, 1)
        # After crossing back to sector 0, last_lap_sectors must be populated
        self.assertEqual(len(st.get_last_lap_sectors(0)), 3)


class TestCatchCalculator(unittest.TestCase):
    def test_first_lap_initialises_pace(self):
        cc = CatchCalculator()
        cc.record_lap(1, 5, 90.0)
        self.assertAlmostEqual(cc.get_pace(1), 90.0)

    def test_ema_blends_subsequent_laps(self):
        cc = CatchCalculator(alpha=0.5)
        cc.record_lap(1, 5, 90.0)
        cc.record_lap(1, 6, 92.0)
        # 0.5*92 + 0.5*90 = 91
        self.assertAlmostEqual(cc.get_pace(1), 91.0)

    def test_lap_below_min_filtered(self):
        cc = CatchCalculator()
        cc.record_lap(1, 5, 5.0)  # Below 10s min
        self.assertIsNone(cc.get_pace(1))

    def test_outlier_slow_lap_filtered(self):
        cc = CatchCalculator(alpha=0.5)
        cc.record_lap(1, 5, 90.0)
        cc.record_lap(1, 6, 200.0)  # Way too slow, must not poison EMA
        self.assertAlmostEqual(cc.get_pace(1), 90.0)

    def test_calc_catch_no_data(self):
        cc = CatchCalculator()
        result = cc.calc_catch_time(None, 1, 2, None, None)
        self.assertIsNone(result)

    def test_calc_catch_with_pace_only(self):
        cc = CatchCalculator()
        # Player faster by 1 s/lap, gap of 5s
        out = cc.calc_catch_time(5.0, 1, 2, my_pace=90.0, other_pace=91.0)
        self.assertIsNotNone(out)
        self.assertTrue(out['gaining'])
        self.assertAlmostEqual(out['gap'], 5.0)


class TestTrackMapper(unittest.TestCase):
    def test_get_position_before_mapping(self):
        tm = TrackMapper()
        self.assertIsNone(tm.get_position(0.5))

    def test_full_synthetic_loop(self):
        """Drive a synthetic square at constant speed and verify finish_mapping."""
        tm = TrackMapper(sample_interval=0.005)
        # Simulate one full lap going through all 100 buckets
        n = 400
        for i in range(n):
            pct = i / n
            tm._world_x = math.cos(2 * math.pi * pct)
            tm._world_z = math.sin(2 * math.pi * pct)
            tm.track_points.append((pct, tm._world_x, tm._world_z))
            tm._sampled_pcts.add(int(pct * 100))
        ok = tm.finish_mapping()
        self.assertTrue(ok)
        self.assertTrue(tm.mapping_complete)
        self.assertIsNotNone(tm.get_position(0.5))
        # LUT must be built
        self.assertIsNotNone(tm._lut)

    def test_continuity_reset_on_dropped_tick(self):
        tm = TrackMapper()
        # Manually seed state as if we'd been running, then simulate a
        # huge time gap (e.g. user paused for 5 seconds).
        tm._last_time = time.time() - 5.0
        tm._last_pct = 0.10
        tm.record_tick(0.11, 30.0, 0.0)  # dt > 0.5 -> dropped
        self.assertIsNone(tm._last_time)
        self.assertIsNone(tm._last_pct)


class TestSanitizeFilename(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_sanitize_filename('Hockenheim GP'), 'hockenheim_gp')

    def test_special_chars(self):
        result = _sanitize_filename('Spa/Francorchamps - GP')
        self.assertNotIn('/', result)
        self.assertTrue(result.startswith('spa'))

    def test_collapses_whitespace(self):
        self.assertEqual(_sanitize_filename('A   B'), 'a_b')


class TestCompass(unittest.TestCase):
    def test_cardinal_points(self):
        self.assertEqual(SessionMonitor._deg_to_compass(0), 'N')
        self.assertEqual(SessionMonitor._deg_to_compass(90), 'O')
        self.assertEqual(SessionMonitor._deg_to_compass(180), 'S')
        self.assertEqual(SessionMonitor._deg_to_compass(270), 'W')

    def test_intercardinals(self):
        self.assertEqual(SessionMonitor._deg_to_compass(45), 'NO')
        self.assertEqual(SessionMonitor._deg_to_compass(135), 'SO')


class TestPitWindow(unittest.TestCase):
    def test_estimate_laps_remaining(self):
        self.assertAlmostEqual(estimate_laps_remaining(50.0, 2.5), 20.0)

    def test_estimate_none_inputs(self):
        self.assertIsNone(estimate_laps_remaining(None, 2.5))
        self.assertIsNone(estimate_laps_remaining(50.0, None))
        self.assertIsNone(estimate_laps_remaining(50.0, 0.0))

    def test_avg_filters_zero(self):
        self.assertAlmostEqual(
            fuel_per_lap_from_history([2.0, 2.5, 0.0, -1.0, 2.5]),
            (2.0 + 2.5 + 2.5) / 3,
        )

    def test_avg_empty(self):
        self.assertIsNone(fuel_per_lap_from_history([0, -1]))

    def test_fuel_for_laps_with_reserve(self):
        self.assertAlmostEqual(fuel_for_laps(2.0, 10, reserve_laps=0.5), 21.0)

    def test_compute_uses_history_first(self):
        out = compute_pit_window(
            fuel_level=50.0,
            fuel_use_per_hour=120.0,
            last_lap_time=90.0,
            lap_fuel_history=[2.0, 2.0, 2.0],
        )
        self.assertEqual(out['source'], 'history')
        self.assertAlmostEqual(out['fuel_per_lap'], 2.0)
        self.assertAlmostEqual(out['laps_remaining'], 25.0)

    def test_compute_falls_back_to_rate(self):
        out = compute_pit_window(
            fuel_level=50.0,
            fuel_use_per_hour=120.0,
            last_lap_time=90.0,
            lap_fuel_history=None,
        )
        self.assertEqual(out['source'], 'rate')
        # 120 L/h * (90/3600) = 3 L/lap
        self.assertAlmostEqual(out['fuel_per_lap'], 3.0)

    def test_compute_no_data(self):
        out = compute_pit_window(None, None, None, None)
        self.assertIsNone(out['fuel_per_lap'])
        self.assertIsNone(out['laps_remaining'])
        self.assertIsNone(out['source'])


if __name__ == '__main__':
    unittest.main()
