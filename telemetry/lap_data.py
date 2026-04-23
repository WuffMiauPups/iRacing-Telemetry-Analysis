"""Pure data helpers for lap telemetry — shared by the static PNG generator
(`lap_analysis.py`) and the interactive GUI (`gui/lap_plot_widget.py`).

Nothing in this module touches matplotlib or any GUI toolkit.
"""

import os
import csv
import numpy as np
from collections import defaultdict


# Channels to plot (csv_column, display_name, unit, invert_y)
CHANNELS = [
    ('Speed_kmh',           'Speed',        'km/h',  False),
    ('Throttle',            'Gas',          '%',     False),
    ('Brake',               'Bremse',       '%',     False),
    ('Gear',                'Gang',         '',      False),
    ('SteeringWheelAngle',  'Lenkung',      'rad',   False),
    ('LapDeltaToBestLap',   'Delta zu Best','s',     False),
]

# Number of resampled points per lap (0-100% track position)
RESAMPLE_POINTS = 500

# SessionFlags bits: prefer irsdk.Flags; hard-coded fallback (verified 2026-04).
try:
    from irsdk import Flags as _IRFlags
    FLAG_GREEN = _IRFlags.green
    FLAG_CHECKERED = _IRFlags.checkered
except Exception:
    FLAG_GREEN = 0x00000004
    FLAG_CHECKERED = 0x00000001


def safe_float(val, default=None):
    if val is None or val == '' or val == 'None':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_bool(val):
    """Parse True/False/1/0/1.0/0.0 (bool, str, number) → bool or None.

    iRacing's DataLogger serialises booleans with Python's default `True`/
    `False` repr, so csv.DictReader returns those as strings.
    """
    if val is None or val == '':
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ('true', '1', '1.0'):
        return True
    if s in ('false', '0', '0.0', 'none'):
        return False
    try:
        return float(s) != 0.0
    except ValueError:
        return None


def load_and_group_laps(csv_path):
    """Load telemetry CSV and group rows by lap.

    Returns dict: lap_num -> list of (pct, {channel: value}) sorted by pct.
    Only includes laps where the car was moving and on track.

    For richer analysis needs (filtering, mini-sectors, G-G), use
    `load_with_metadata()` which also returns per-lap pit/flag metadata.
    """
    laps, _, _ = load_with_metadata(csv_path)
    return laps


def load_with_metadata(csv_path):
    """Load + group laps; also return per-lap metadata and race-flag bounds.

    Returns (laps, lap_meta, race_bounds):
      laps         dict lap_num -> list of (pct, values) sorted by pct. Each
                   values dict contains every CHANNELS key plus the extras
                   used by mini_sectors / variance_analysis / viewers:
                   LapCurrentLapTime, LatAccel, LonAccel, SteeringWheelTorque.
      lap_meta     dict lap_num -> {'on_pit_road': bool,
                                    'start_session_time', 'end_session_time'}
      race_bounds  (first_green_session_time, first_checkered_session_time)
                   or (None, None) if the flags never fire.
    """
    laps = defaultdict(list)
    lap_meta = defaultdict(lambda: {
        'on_pit_road': False,
        'start_session_time': None,
        'end_session_time': None,
    })
    first_green_st = None
    first_checkered_st = None

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            lap = safe_float(row.get('Lap'))
            pct = safe_float(row.get('LapDistPct'))
            speed = safe_float(row.get('Speed_kmh'), 0)
            session_time = safe_float(row.get('SessionTime'))
            flags_raw = safe_float(row.get('SessionFlags'))
            on_pit = safe_bool(row.get('OnPitRoad'))

            # Race-bound detection uses ALL rows; flags are valid even when
            # the player is stationary (pit box, grid, etc.).
            if session_time is not None and flags_raw is not None:
                flags_int = int(flags_raw)
                if first_green_st is None and (flags_int & FLAG_GREEN):
                    first_green_st = session_time
                if first_checkered_st is None and (flags_int & FLAG_CHECKERED):
                    first_checkered_st = session_time

            if lap is None or pct is None or pct < 0:
                continue
            if speed < 5:
                continue

            lap = int(lap)
            values = {}
            for col, _, _, _ in CHANNELS:
                val = safe_float(row.get(col))
                if col in ('Throttle', 'Brake'):
                    if val is not None:
                        val = val * 100.0
                values[col] = val

            # Extras needed by mini_sectors + variance + G-G scatter.
            values['LapCurrentLapTime'] = safe_float(row.get('LapCurrentLapTime'))
            values['LatAccel'] = safe_float(row.get('LatAccel'))
            values['LonAccel'] = safe_float(row.get('LonAccel'))
            values['SteeringWheelTorque'] = safe_float(row.get('SteeringWheelTorque'))

            laps[lap].append((pct, values))

            meta = lap_meta[lap]
            if on_pit is True:
                meta['on_pit_road'] = True
            if session_time is not None:
                if meta['start_session_time'] is None or session_time < meta['start_session_time']:
                    meta['start_session_time'] = session_time
                if meta['end_session_time'] is None or session_time > meta['end_session_time']:
                    meta['end_session_time'] = session_time

    for lap_num in laps:
        laps[lap_num].sort(key=lambda x: x[0])

    return dict(laps), dict(lap_meta), (first_green_st, first_checkered_st)


def filter_laps(laps, lap_meta, race_bounds, session_type, include_all=False):
    """Drop outlap/inlap/start/finish laps based on OnPitRoad + SessionFlags.

    Green/checkered are only reliable in Race sessions; in Practice/Qualifying
    the checkered bit fires for non-finish reasons, so we ignore flags outside
    Race and fall back to 'drop first lap only'.

    Returns (filtered_laps_dict, reasons_dict: lap_num -> reason_skipped).
    """
    reasons = {}
    if include_all:
        return dict(laps), reasons

    green_st, checkered_st = race_bounds
    is_race = (session_type or '').lower().startswith('race')
    if not is_race:
        green_st = None
        checkered_st = None

    # "Starting lap" = first full lap whose start is >= green flag time.
    starting_lap = None
    if green_st is not None:
        candidates = [ln for ln in laps
                      if (lap_meta.get(ln, {}).get('start_session_time') is not None
                          and lap_meta[ln]['start_session_time'] >= green_st)]
        if candidates:
            starting_lap = min(candidates)

    first_lap_fallback = min(laps.keys()) if laps else None

    kept = {}
    for lap_num, ticks in laps.items():
        meta = lap_meta.get(lap_num, {})
        if meta.get('on_pit_road'):
            reasons[lap_num] = 'pit (out/in lap)'
            continue

        end_st = meta.get('end_session_time')
        start_st = meta.get('start_session_time')

        if green_st is not None and end_st is not None and end_st <= green_st:
            reasons[lap_num] = 'before green flag'
            continue
        if starting_lap is not None and lap_num == starting_lap:
            reasons[lap_num] = 'starting lap (first after green)'
            continue
        if checkered_st is not None and end_st is not None and end_st > checkered_st:
            reasons[lap_num] = 'finishing lap (spans/after checkered)'
            continue

        # Non-Race fallback: drop only the very first lap (outlap/warmup).
        if not is_race and lap_num == first_lap_fallback:
            reasons[lap_num] = 'first lap of session (no flag data)'
            continue

        kept[lap_num] = ticks

    return kept, reasons


def resample_lap(lap_data, channel, num_points=RESAMPLE_POINTS):
    """Resample a lap's channel data to evenly spaced track positions.

    Returns (pct_array, value_array) with num_points entries, or (None, None)
    if there is not enough data.
    """
    pcts = [p for p, v in lap_data if v.get(channel) is not None]
    vals = [v[channel] for p, v in lap_data if v.get(channel) is not None]

    if len(pcts) < 10:
        return None, None

    target_pcts = np.linspace(min(pcts), max(pcts), num_points)
    resampled = np.interp(target_pcts, pcts, vals)

    return target_pcts * 100, resampled


def find_best_lap(lap_times):
    """Return the lap number with the fastest valid time, or None."""
    best_time = float('inf')
    best_lap = None
    for lap_num, lap_time in lap_times.items():
        if lap_time and 0 < lap_time < best_time:
            best_time = lap_time
            best_lap = lap_num
    return best_lap


def get_lap_times_from_summary(session_dir):
    """Read per-lap times from `lap_summary.csv` in the session directory.

    Returned keys are 0-based to match the tick-data `Lap` column
    (`continuous_lap`). `data_logger.py` writes the summary `Lap` as
    `continuous_lap + 1` (1-based for display), so we subtract one here
    — that way callers can look up times by the same lap number they
    find in `load_and_group_laps()` / `load_with_metadata()`.
    """
    lap_times = {}
    summary_path = os.path.join(session_dir, 'lap_summary.csv')
    if not os.path.exists(summary_path):
        return lap_times

    with open(summary_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            lap = safe_float(row.get('Lap'))
            lt = safe_float(row.get('LapTime'))
            if lap is not None and lt is not None and lt > 0:
                lap_times[int(lap) - 1] = lt

    return lap_times
