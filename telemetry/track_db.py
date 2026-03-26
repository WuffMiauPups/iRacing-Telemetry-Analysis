import json
import os
import re


# Database folder next to this file's package
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'track_db')


def _sanitize_filename(name):
    """Convert a track name + config to a safe filename."""
    # Replace special chars with underscores, collapse multiples
    safe = re.sub(r'[^\w\s-]', '_', name)
    safe = re.sub(r'[\s]+', '_', safe)
    safe = re.sub(r'_+', '_', safe).strip('_')
    return safe.lower()


def get_track_key(weekend_info):
    """Build a unique key from the track name and config.

    Uses TrackDisplayName + TrackConfigName so that e.g.
    'Hockenheim GP' and 'Hockenheim National' are separate entries.
    """
    if weekend_info is None:
        return None

    track_name = weekend_info.get('TrackDisplayName', '')
    track_config = weekend_info.get('TrackConfigName', '')

    if not track_name:
        return None

    if track_config:
        return f'{track_name} - {track_config}'
    return track_name


def save_track(track_key, normalized_points):
    """Save a track layout to the database.

    Args:
        track_key: e.g. 'Hockenheimring Baden-Württemberg - Grand Prix'
        normalized_points: List of (pct, x, y) tuples from TrackMapper
    """
    os.makedirs(DB_DIR, exist_ok=True)

    filename = _sanitize_filename(track_key) + '.json'
    filepath = os.path.join(DB_DIR, filename)

    data = {
        'track_name': track_key,
        'point_count': len(normalized_points),
        'points': [[pct, x, y] for pct, x, y in normalized_points],
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    return filepath


def load_track(track_key):
    """Load a track layout from the database.

    Returns list of (pct, x, y) tuples, or None if not found.
    """
    filename = _sanitize_filename(track_key) + '.json'
    filepath = os.path.join(DB_DIR, filename)

    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        points = [(p[0], p[1], p[2]) for p in data['points']]
        return points
    except Exception:
        return None


def list_tracks():
    """List all saved tracks in the database."""
    if not os.path.exists(DB_DIR):
        return []

    tracks = []
    for f in os.listdir(DB_DIR):
        if f.endswith('.json'):
            try:
                filepath = os.path.join(DB_DIR, f)
                with open(filepath, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                tracks.append({
                    'name': data.get('track_name', f),
                    'points': data.get('point_count', 0),
                    'file': f,
                })
            except Exception:
                pass
    return tracks
