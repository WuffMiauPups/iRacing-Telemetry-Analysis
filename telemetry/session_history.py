"""Cross-session personal-best tracker.

Walks the race_logs/ directory, reads each session's lap_summary.csv and
session_meta.json (or folder name fallback), and computes the all-time
fastest lap per track. Used on the Live tab to show "PB Hockenheim:
1:30.12 (vor 5 Tagen)" when you return to a track you've driven before.
"""

import os
import re
from datetime import datetime

from telemetry.session_meta import load_session_meta
from telemetry.lap_data import get_lap_times_from_summary


# Folder suffixes for session-type parsing. Ordered longest-first so
# e.g. "_Lone_Qualify" matches before "_Qualify".
_SESSION_SUFFIXES = ('Offline_Testing', 'Lone_Qualify', 'Time_Attack',
                     'Practice', 'Qualify', 'Race')


def _folder_track_name(folder_basename):
    """Extract a track name from '2026-04-20_18-46_<track>_<session_type>'.

    Returns None if parsing fails. Strips known session-type suffixes.
    """
    # Strip the 'YYYY-MM-DD_HH-MM_' prefix (16 chars + underscore).
    m = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', folder_basename)
    if not m:
        return None
    rest = m.group(1)
    for suffix in _SESSION_SUFFIXES:
        if rest.endswith('_' + suffix):
            return rest[:-(len(suffix) + 1)].replace('_', ' ')
    return rest.replace('_', ' ')


def _folder_date(folder_basename):
    """Return datetime parsed from folder name, or None."""
    m = re.match(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})_', folder_basename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y-%m-%d_%H-%M')
    except ValueError:
        return None


def scan_personal_bests(race_logs_dir):
    """Walk all session folders and return per-track PBs.

    Returns: dict[track_name] -> {
        'best_time': float,           # fastest lap across all sessions
        'session_dir': str,           # absolute path of the session
        'date': datetime | None,      # session start time
        'lap_num': int,               # 0-based tick-data lap number
    }
    """
    if not os.path.isdir(race_logs_dir):
        return {}

    pbs = {}
    for name in os.listdir(race_logs_dir):
        session_dir = os.path.join(race_logs_dir, name)
        if not os.path.isdir(session_dir):
            continue

        meta = load_session_meta(session_dir) or {}
        track = meta.get('track_name') or _folder_track_name(name)
        if not track:
            continue

        lap_times = get_lap_times_from_summary(session_dir)
        if not lap_times:
            continue

        # Find fastest lap in this session.
        best_ln, best_t = None, None
        for ln, t in lap_times.items():
            if t is None or t <= 0:
                continue
            if best_t is None or t < best_t:
                best_t = t
                best_ln = ln
        if best_t is None:
            continue

        prev = pbs.get(track)
        if prev is None or best_t < prev['best_time']:
            pbs[track] = {
                'best_time': best_t,
                'session_dir': session_dir,
                'date': _folder_date(name),
                'lap_num': best_ln,
            }
    return pbs


def get_track_pb(race_logs_dir, track_name):
    """Convenience: PB for one track only. None if never driven."""
    if not track_name:
        return None
    return scan_personal_bests(race_logs_dir).get(track_name)
