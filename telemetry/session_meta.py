"""Per-session metadata written at session start.

Saved as session_meta.json in the session directory. Needed so that
post-session reanalysis can reload the track outline (via track_key)
and apply session-type-aware filtering without requiring an active
iRacing connection.
"""

import json
import os
import sys
from datetime import datetime


SCHEMA_VERSION = 1
FILENAME = 'session_meta.json'


def write_session_meta(session_dir, conn, track_key, track_name, session_type):
    """Write session_meta.json to the session directory.

    Best-effort: fields that can't be resolved (missing YAML, disconnect)
    are stored as None but the file is always written.
    """
    track_config_name = None
    car_name = None
    player_car_idx = None

    try:
        wi = conn.weekend_info
        if wi:
            track_config_name = wi.get('TrackConfigName')
    except Exception as e:
        print(f"[session_meta] reading weekend_info: {e}", file=sys.stderr)

    try:
        di = conn.driver_info
        if di:
            player_car_idx = di.get('DriverCarIdx')
            drivers = di.get('Drivers') or []
            if player_car_idx is not None and 0 <= player_car_idx < len(drivers):
                car_name = drivers[player_car_idx].get('CarScreenName')
    except Exception as e:
        print(f"[session_meta] reading driver_info: {e}", file=sys.stderr)

    meta = {
        'schema_version': SCHEMA_VERSION,
        'track_key': track_key,
        'track_name': track_name,
        'track_config_name': track_config_name,
        'session_type': session_type,
        'session_start_iso': datetime.now().isoformat(timespec='seconds'),
        'car_name': car_name,
        'player_car_idx': player_car_idx,
    }

    path = os.path.join(session_dir, FILENAME)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[session_meta] writing {path}: {e}", file=sys.stderr)


def load_session_meta(session_dir):
    """Load session_meta.json. Returns dict, or None if missing/malformed."""
    path = os.path.join(session_dir, FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[session_meta] loading {path}: {e}", file=sys.stderr)
        return None
