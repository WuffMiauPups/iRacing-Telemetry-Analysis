"""LogBrowserModel — scans race_logs/ and exposes sessions in a QStandardItemModel.

Folder-name convention created by `main.py` / `gui/worker.py`:

    YYYY-MM-DD_HH-MM_<Track>_<SessionType>

Where `<SessionType>` may itself contain underscores (e.g. `Lone_Qualify`,
`Offline_Testing`). We use a whitelist of known suffixes so track names with
underscores don't confuse the split.
"""

import os
import re
import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel, QStandardItem


SESSION_SUFFIXES = (
    'Lone_Qualify',
    'Open_Qualify',
    'Offline_Testing',
    'Practice',
    'Race',
    'Qualify',
    'Session',
    'Warmup',
    'Heat',
)

_DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})_(.+)$')


def parse_folder_name(name):
    """Return dict {date, time, track, session_type} or None if it doesn't match."""
    m = _DATE_RE.match(name)
    if not m:
        return None
    date, tm, rest = m.groups()

    session_type = 'Session'
    track = rest
    # Match longest suffix first (Lone_Qualify before Qualify).
    for suffix in sorted(SESSION_SUFFIXES, key=len, reverse=True):
        needle = '_' + suffix
        if rest.endswith(needle):
            session_type = suffix.replace('_', ' ')
            track = rest[: -len(needle)]
            break
    return {
        'date': date,
        'time': tm.replace('-', ':'),
        'track': track.replace('_', ' '),
        'session_type': session_type,
    }


def _count_laps(session_dir):
    path = os.path.join(session_dir, 'lap_summary.csv')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            _ = next(reader, None)  # header
            return sum(1 for _ in reader)
    except Exception:
        return None


class LogBrowserModel(QStandardItemModel):
    PATH_ROLE = Qt.UserRole + 1

    def __init__(self, race_logs_dir, parent=None):
        super().__init__(parent)
        self._race_logs_dir = race_logs_dir
        self.setHorizontalHeaderLabels(['Strecke / Session', 'Datum', 'Runden'])

    def rescan(self):
        self.clear()
        self.setHorizontalHeaderLabels(['Strecke / Session', 'Datum', 'Runden'])
        root = self.invisibleRootItem()

        if not os.path.isdir(self._race_logs_dir):
            return

        # Group by track name
        by_track = {}
        for name in sorted(os.listdir(self._race_logs_dir), reverse=True):
            full = os.path.join(self._race_logs_dir, name)
            if not os.path.isdir(full):
                continue
            info = parse_folder_name(name)
            if info is None:
                continue
            info['path'] = full
            info['folder'] = name
            info['laps'] = _count_laps(full)
            by_track.setdefault(info['track'], []).append(info)

        for track in sorted(by_track.keys()):
            sessions = by_track[track]
            sessions.sort(key=lambda x: (x['date'], x['time']), reverse=True)

            track_item = QStandardItem(track)
            track_item.setEditable(False)
            root.appendRow([track_item, QStandardItem(''), QStandardItem('')])

            for s in sessions:
                name_item = QStandardItem(f"{s['session_type']}")
                name_item.setEditable(False)
                name_item.setData(s['path'], self.PATH_ROLE)

                date_item = QStandardItem(f"{s['date']}  {s['time']}")
                date_item.setEditable(False)

                laps_item = QStandardItem('—' if s['laps'] is None else str(s['laps']))
                laps_item.setEditable(False)

                if s['laps'] is None:
                    for it in (name_item, date_item, laps_item):
                        it.setFlags(it.flags() & ~Qt.ItemIsEnabled)

                track_item.appendRow([name_item, date_item, laps_item])

    def session_path(self, index):
        """Return the session folder path for the given model index, or None."""
        if not index.isValid():
            return None
        # Fetch the name column (col 0) sibling — where we stored the path.
        sibling = index.sibling(index.row(), 0)
        data = self.itemFromIndex(sibling)
        if data is None:
            return None
        return data.data(self.PATH_ROLE)
