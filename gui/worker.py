"""Telemetry worker for the PySide6 GUI.

Runs the 10 Hz iRacing polling loop in its own QThread, builds a snapshot
dict each tick, and emits it as a Qt signal for GUI consumption. The loop
logic is lifted from main.py and adapted so the worker can shut down
responsively.
"""

import os
import time
import copy
from datetime import datetime

from PySide6.QtCore import QObject, Signal, Slot

import config
from telemetry.connection import IRacingConnection
from telemetry.timing import TimingMonitor
from telemetry.session import SessionMonitor
from telemetry.track_map import TrackMapper
from telemetry.track_db import get_track_key, load_sector_splits
from telemetry.data_logger import DataLogger
from telemetry.session_summary import generate_session_summary
from telemetry.lap_analysis import generate_lap_analysis
from telemetry.session_meta import write_session_meta
from telemetry.session_history import get_track_pb


RACE_LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'race_logs')

# Auto-finalize + stop the worker this many seconds after iRacing drops,
# provided at least one completed lap was recorded.
AUTO_FINALIZE_DISCONNECT_SEC = 5


def _detect_session_type(conn):
    try:
        si = conn.session_info
        if si and 'Sessions' in si:
            sessions = si['Sessions']
            session_num = conn.get('SessionNum')
            if session_num is not None and session_num < len(sessions):
                return sessions[session_num].get('SessionType', 'Session')
    except Exception:
        pass
    return 'Session'


def _create_session_dir(track_name, session_type):
    date_str = datetime.now().strftime('%Y-%m-%d_%H-%M')
    safe_track = ''.join(c if c.isalnum() or c in ' -_' else '_'
                         for c in (track_name or 'Unknown'))
    safe_track = safe_track.strip().replace('  ', ' ').replace(' ', '_')
    safe_session = (session_type or 'Session').replace(' ', '_')

    folder = f'{date_str}_{safe_track}_{safe_session}'
    path = os.path.join(RACE_LOGS_DIR, folder)
    if os.path.exists(path):
        suffix = 2
        while os.path.exists(f'{path}_{suffix}'):
            suffix += 1
        path = f'{path}_{suffix}'
    os.makedirs(path, exist_ok=True)
    return path


class TelemetryWorker(QObject):
    """Owns the iRacing connection + telemetry state. Emits snapshot(dict)."""

    snapshot = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

        # Lazily created so a paused-not-connected state is possible.
        self.conn = None
        self.timing = None
        self.session = None
        self.track_mapper = None
        self.data_logger = None

        self._track_name = 'Unknown'
        self._session_type = 'Session'
        self._session_dir = None
        self._track_saved = False
        self._track_pb = None
        self._cached_gap_ahead = None
        self._cached_gap_behind = None
        self._last_session_refresh = 0
        # Live sector-delta tracking (3 main sectors).
        self._session_sector_bests = [None, None, None]
        self._last_completed_sector_count = 0
        self._last_sector_delta = None  # dict or None

    @Slot()
    def run(self):
        """Entry point posted by QThread.started. Runs until stop()."""
        try:
            self._setup()
            self._main_loop()
        except Exception as e:
            print(f"[worker] Loop aborted: {e}")
        finally:
            self._shutdown()

    def stop(self):
        self._stop = True

    # ------------------------------------------------------------------
    # Setup / shutdown
    # ------------------------------------------------------------------
    def _setup(self):
        self.conn = IRacingConnection()
        self._emit_waiting("Warte auf iRacing …")
        self._poll_startup()
        if self._stop:
            return

        _track_key = get_track_key(self.conn.weekend_info)
        splits = load_sector_splits(_track_key) if _track_key else None

        self.timing = TimingMonitor(self.conn, sector_splits=splits)
        self.session = SessionMonitor(self.conn)
        self.track_mapper = TrackMapper()

        if _track_key and self.track_mapper.load_from_db(_track_key):
            self._track_saved = True

        sess_info = self.session.get_session_info() or {}
        self._track_name = sess_info.get('track_name', 'Unknown')
        self._session_type = _detect_session_type(self.conn)
        self._session_dir = _create_session_dir(self._track_name, self._session_type)
        write_session_meta(self._session_dir, self.conn, _track_key,
                            self._track_name, self._session_type)
        self.data_logger = DataLogger(self._session_dir)
        self._track_key = _track_key
        # All-time PB at this track (scanned once at session start).
        try:
            self._track_pb = get_track_pb(RACE_LOGS_DIR, self._track_name)
        except Exception as e:
            print(f"[worker] track PB lookup failed: {e}")
            self._track_pb = None

    def _poll_startup(self):
        """Like conn.connect(), but respects the stop flag so window-close
        doesn't hang on a 2-second sleep inside pyirsdk."""
        while not self._stop:
            try:
                if self.conn.ir.startup():
                    self.conn.connected = True
                    self.conn._last_reinit = time.time()
                    self.conn._cache_session_data()
                    return
            except Exception:
                pass
            # Short sleeps so stop() is responsive.
            for _ in range(10):
                if self._stop:
                    return
                time.sleep(0.2)

    def _shutdown(self):
        # Session summaries (mirrors main.py finally block).
        if self.data_logger is not None and self._session_dir is not None:
            try:
                lap_data = self.data_logger.get_lap_data()
                if lap_data:
                    generate_session_summary(
                        self._session_dir, lap_data,
                        track_name=self._track_name,
                        session_type=self._session_type,
                    )
                    generate_lap_analysis(self._session_dir)
            except Exception as e:
                print(f"[worker] Summary generation failed: {e}")
            try:
                self.data_logger.close()
            except Exception:
                pass

        if self.conn is not None:
            try:
                self.conn.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _main_loop(self):
        disconnect_at = None  # set when iRacing drops; reset on reconnect
        while not self._stop:
            now = time.time()
            try:
                is_connected = self.conn.check_connection()
            except Exception:
                # If check_connection itself blows up, sleep briefly and retry.
                time.sleep(0.5)
                continue

            # Auto-finalize on sustained disconnect with recorded lap data.
            if not is_connected:
                if disconnect_at is None:
                    disconnect_at = now
                elif (now - disconnect_at >= AUTO_FINALIZE_DISCONNECT_SEC
                      and self.data_logger is not None
                      and self.data_logger._continuous_lap > 0):
                    print(f"[worker] iRacing seit {now - disconnect_at:.0f}s offline, "
                          f"{self.data_logger._continuous_lap} Runde(n) aufgezeichnet. "
                          f"Session wird automatisch finalisiert.")
                    self._emit_waiting(
                        "Session beendet — Analyse verfügbar im Analyze-Tab.")
                    return  # run()'s finally runs _shutdown → plots + summary
                # Still disconnected: show waiting + poll.
                self._emit_waiting("Warte auf iRacing …")
                time.sleep(config.TICK_RATE)
                continue
            elif disconnect_at is not None:
                print(f"[worker] iRacing reconnected after {now - disconnect_at:.0f}s.")
                disconnect_at = None

            on_track = self.conn.get('IsOnTrack')
            on_track = bool(on_track) if on_track is not None else False
            player_idx = self.conn.get('PlayerCarIdx')

            if now - self._last_session_refresh > config.SESSION_REFRESH:
                self.conn.refresh_session_data()
                self._last_session_refresh = now
                # Session type can change between sessions (Practice→Qual→Race)
                self._session_type = _detect_session_type(self.conn)

            snap = self._build_snapshot(on_track, player_idx)
            self.snapshot.emit(snap)

            time.sleep(config.TICK_RATE)

    def _build_snapshot(self, on_track, player_idx):
        """Build one snapshot dict from the current iRacing state."""
        snap = {
            'connected': bool(self.conn and self.conn.connected),
            'on_track': on_track,
            'player_idx': player_idx,
            'session_info': self.session.get_session_info() if self.session else None,
            'session_type': self._session_type,
            'timing_data': None,
            'qual_data': None,
            'track_outline': None,
            'cars': [],
            'map_status': 'Nicht auf der Strecke',
            'track_pb': self._track_pb,
            'track_name': self._track_name,
            'car_status': {
                'rpm': self.conn.get('RPM') if self.conn else None,
                'oil_temp': self.conn.get('OilTemp') if self.conn else None,
                'water_temp': self.conn.get('WaterTemp') if self.conn else None,
                'voltage': self.conn.get('Voltage') if self.conn else None,
                'rpm_redline': (self.conn.get('DriverCarRedLine')
                                or self.conn.get('DriverCarSLShiftRPM')
                                if self.conn else None),
            },
        }

        if not (on_track and player_idx is not None):
            return snap

        # CSV logging at 10 Hz
        try:
            self.data_logger.log_tick(self.conn, player_idx,
                                      gap_ahead=self._cached_gap_ahead,
                                      gap_behind=self._cached_gap_behind)
        except Exception:
            pass

        # Track mapping (first lap only)
        if not self.track_mapper.mapping_complete:
            lap_dist_pcts = self.conn.get('CarIdxLapDistPct')
            try:
                player_pct = lap_dist_pcts[player_idx] if lap_dist_pcts else None
            except (IndexError, TypeError):
                player_pct = None
            speed = self.conn.get('Speed')
            yaw_north = self.conn.get('YawNorth') or self.conn.get('Yaw')
            if player_pct is not None and speed is not None and yaw_north is not None:
                self.track_mapper.record_tick(player_pct, speed, yaw_north)
                if self.track_mapper.try_finish_mapping() and not self._track_saved:
                    if self._track_key:
                        self.track_mapper.save_to_db(self._track_key)
                        self._track_saved = True
            snap['map_status'] = f"Erfasse … {self.track_mapper.coverage_pct}% Abdeckung"

        # Map: track outline + live cars
        if self.track_mapper.mapping_complete:
            snap['track_outline'] = list(self.track_mapper.get_track_outline())
            positions = self.conn.get('CarIdxPosition')
            lap_dist_pcts = self.conn.get('CarIdxLapDistPct')
            cars = []
            if positions and lap_dist_pcts:
                for car_idx in range(len(positions)):
                    pos = positions[car_idx]
                    if pos is None or pos <= 0:
                        continue
                    if car_idx >= len(lap_dist_pcts):
                        continue
                    pct = lap_dist_pcts[car_idx]
                    if pct is None or pct < 0:
                        continue
                    xy = self.track_mapper.get_position(pct)
                    if xy is None:
                        continue
                    x, y = xy
                    cars.append({
                        'x': x,
                        'y': y,
                        'car_number': self.timing.get_car_number(car_idx),
                        'driver_name': self.timing.get_driver_name(car_idx),
                        'position': pos,
                        'is_player': car_idx == player_idx,
                    })
            snap['cars'] = cars
            src = 'DB' if self._track_saved else 'Live'
            snap['map_status'] = f"Map aktiv [{src}] ({self.track_mapper.point_count} Punkte)"

        # Timing data (expensive YAML-touching bits)
        try:
            self.timing.update_sectors()
            timing_data = self.timing.get_timing_data()
            snap['timing_data'] = timing_data
            if timing_data:
                ca = timing_data.get('catch_ahead')
                cb = timing_data.get('catch_behind')
                self._cached_gap_ahead = ca.get('gap') if ca else None
                self._cached_gap_behind = cb.get('gap') if cb else None
        except Exception:
            pass

        # Live sector-delta: detect newly completed sectors and update bests.
        self._update_sector_delta(player_idx)
        snap['sector_delta'] = self._last_sector_delta
        snap['sector_bests'] = list(self._session_sector_bests)

        # Qualifying display data (best/last/delta)
        snap['qual_data'] = self._build_qual_data(snap.get('timing_data'), player_idx)

        return snap

    def _update_sector_delta(self, player_idx):
        """Detect sector completions via SectorTracker's completed_sectors
        list. Populate self._last_sector_delta with the most recent one."""
        try:
            current = self.timing.sector_tracker.get_current_sectors(player_idx) or []
        except Exception:
            return

        prev_count = self._last_completed_sector_count
        curr_count = len(current)

        newly_completed = []  # list of (sector_idx_0_based, time)
        if curr_count > prev_count:
            # One sector just completed mid-lap (idx 0 or 1 for 3-sector setup).
            newly_completed.append((curr_count - 1, current[-1]))
        elif curr_count == 0 and prev_count > 0:
            # S/F crossed — the final sector (last one) just landed in
            # last_lap_sectors. Pull its time from there.
            last = self.timing.sector_tracker.get_last_lap_sectors(player_idx) or []
            n = self.timing.sector_tracker.num_sectors
            if len(last) == n:
                newly_completed.append((n - 1, last[-1]))

        self._last_completed_sector_count = curr_count

        for idx, t in newly_completed:
            # Grow the bests list if the tracker has more sectors than expected.
            while len(self._session_sector_bests) <= idx:
                self._session_sector_bests.append(None)
            prev_best = self._session_sector_bests[idx]
            is_new_best = (prev_best is None) or (t < prev_best)
            if is_new_best:
                self._session_sector_bests[idx] = t
            self._last_sector_delta = {
                'sector_idx': idx,
                'time': t,
                'best': self._session_sector_bests[idx],
                'delta': 0.0 if is_new_best else (t - prev_best),
                'is_new_best': is_new_best,
            }

    def _build_qual_data(self, timing_data, player_idx):
        """Compact dict specifically for QualifyingPanel."""
        player = (timing_data or {}).get('player') if timing_data else None
        best = None
        last = None
        if player:
            best = player.get('best_lap')
            last = player.get('last_lap')
        # Fall back to the sim's personal best if timing_data was unavailable.
        if best is None:
            b = self.conn.get('LapBestLapTime')
            if b is not None and b > 0:
                best = b
        if last is None:
            l = self.conn.get('LapLastLapTime')
            if l is not None and l > 0:
                last = l

        delta = None
        if best is not None and last is not None:
            delta = last - best

        sectors_last = []
        sectors_best_total = None
        if player_idx is not None and self.timing is not None:
            try:
                sectors_last = list(self.timing.sector_tracker.get_last_lap_sectors(player_idx) or [])
                if sectors_last:
                    sectors_best_total = sum(sectors_last)
            except Exception:
                sectors_last = []

        return {
            'best_lap': best,
            'last_lap': last,
            'delta': delta,
            'sectors_last': sectors_last,
            'sectors_best_total': sectors_best_total,
        }

    def _emit_waiting(self, msg):
        self.snapshot.emit({
            'connected': False,
            'on_track': False,
            'player_idx': None,
            'session_info': None,
            'session_type': '',
            'timing_data': None,
            'qual_data': None,
            'track_outline': None,
            'cars': [],
            'map_status': msg,
        })
