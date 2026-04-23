import time
import os
from datetime import datetime

from rich.live import Live

import config
from telemetry.connection import IRacingConnection
from telemetry.timing import TimingMonitor
from telemetry.session import SessionMonitor
from telemetry.track_map import TrackMapper
from telemetry.track_db import get_track_key, load_sector_splits
from telemetry.data_logger import DataLogger
from telemetry.session_summary import generate_session_summary
from telemetry.lap_analysis import generate_lap_analysis
from telemetry.pit_window import compute_pit_window
from telemetry.tires import read_tires, has_any_data as tires_has_data
from display.renderer import Renderer
from display.map_window import MapWindow


TICK_RATE = config.TICK_RATE
DISPLAY_RATE = config.DISPLAY_RATE
SESSION_REFRESH = config.SESSION_REFRESH

# Base directory for race logs
RACE_LOGS_DIR = os.path.join(os.path.dirname(__file__), 'race_logs')


def _detect_session_type(conn):
    """Best-effort detection of the current iRacing session type.

    Returns the SessionType string from the iRacing session YAML for the
    currently active session, or 'Session' as a neutral fallback. Any
    exception while reading the YAML is intentionally swallowed — this
    matches the original inline behavior.
    """
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
    """Create a unique folder for this session.

    Format: race_logs/2026-03-24_Hockenheim_Race/
    """
    date_str = datetime.now().strftime('%Y-%m-%d_%H-%M')
    # Sanitize track name for filesystem
    safe_track = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in (track_name or 'Unknown'))
    safe_track = safe_track.strip().replace('  ', ' ').replace(' ', '_')
    safe_session = (session_type or 'Session').replace(' ', '_')

    folder_name = f'{date_str}_{safe_track}_{safe_session}'
    session_dir = os.path.join(RACE_LOGS_DIR, folder_name)
    # Avoid silently overwriting an existing session if two runs land in
    # the same minute. Original behavior is preserved when there is no
    # collision (same path returned). On collision a numeric suffix is
    # appended.
    if os.path.exists(session_dir):
        suffix = 2
        while True:
            candidate = f'{session_dir}_{suffix}'
            if not os.path.exists(candidate):
                session_dir = candidate
                break
            suffix += 1
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def main():
    conn = IRacingConnection()
    print("iRacing Telemetry Tool startet...")
    conn.connect()
    print("Verbunden mit iRacing!")

    # Per-track sector splits if defined in track_db, else 3 equal sectors
    _track_key_for_sectors = get_track_key(conn.weekend_info)
    _custom_splits = load_sector_splits(_track_key_for_sectors) if _track_key_for_sectors else None

    timing = TimingMonitor(conn, sector_splits=_custom_splits)
    session = SessionMonitor(conn)
    track_mapper = TrackMapper()
    renderer = Renderer()

    map_window = MapWindow()
    map_window.start()

    # Try to load saved track layout from database
    track_key = get_track_key(conn.weekend_info)
    track_saved = False
    if track_key:
        if track_mapper.load_from_db(track_key):
            print(f"Track Map geladen: {track_key}")
        else:
            print(f"Keine gespeicherte Map fuer: {track_key} -- Mapping-Runde noetig")

    # Create session directory and data logger
    session_info = session.get_session_info()
    track_name = session_info.get('track_name', 'Unknown') if session_info else 'Unknown'
    # Try to detect session type from iRacing
    session_type = _detect_session_type(conn)

    session_dir = _create_session_dir(track_name, session_type)
    data_logger = DataLogger(session_dir)
    print(f"Logging nach: {session_dir}")

    last_display_time = 0
    last_session_refresh = 0

    # Cache for gap values (updated at display rate, used by logger)
    cached_gap_ahead = None
    cached_gap_behind = None

    # Display cache
    display_cache = {
        'timing_data': None,
        'weather_data': None,
        'session_info': None,
        'map_status': None,
        'pit_data': None,
        'tire_data': None,
    }

    try:
        with Live(renderer.render(), console=renderer.console, refresh_per_second=2, screen=True) as live:
            while True:
                now = time.time()
                conn.check_connection()
                map_window.ensure_running()

                on_track = conn.get('IsOnTrack')
                on_track = bool(on_track) if on_track is not None else False
                player_idx = conn.get('PlayerCarIdx')

                # Refresh session data periodically
                if now - last_session_refresh > SESSION_REFRESH:
                    conn.refresh_session_data()
                    last_session_refresh = now

                # --- Fast telemetry (10Hz) ---
                if on_track and player_idx is not None:

                    # CSV logging — every tick with all telemetry
                    data_logger.log_tick(conn, player_idx,
                                        gap_ahead=cached_gap_ahead,
                                        gap_behind=cached_gap_behind)

                    # Track map recording
                    if not track_mapper.mapping_complete:
                        lap_dist_pcts = conn.get('CarIdxLapDistPct')
                        try:
                            player_pct = lap_dist_pcts[player_idx] if lap_dist_pcts else None
                        except (IndexError, TypeError):
                            player_pct = None
                        speed = conn.get('Speed')
                        yaw_north = conn.get('YawNorth')
                        if yaw_north is None:
                            yaw_north = conn.get('Yaw')

                        if player_pct is not None and speed is not None and yaw_north is not None:
                            track_mapper.record_tick(player_pct, speed, yaw_north)

                            if track_mapper.try_finish_mapping() and not track_saved:
                                if track_key:
                                    track_mapper.save_to_db(track_key)
                                    track_saved = True

                        coverage = track_mapper.check_coverage()
                        map_window.update_data(None, [], mapping_progress=coverage)

                    # Track map: update car positions
                    if track_mapper.mapping_complete:
                        positions = conn.get('CarIdxPosition')
                        lap_dist_pcts = conn.get('CarIdxLapDistPct')
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
                                xy = track_mapper.get_position(pct)
                                if xy is None:
                                    continue
                                x, y = xy
                                cars.append({
                                    'x': x,
                                    'y': y,
                                    'car_number': timing.get_car_number(car_idx),
                                    'driver_name': timing.get_driver_name(car_idx),
                                    'position': pos,
                                    'is_player': car_idx == player_idx,
                                })

                        map_window.update_data(track_mapper.get_track_outline(), cars)

                # --- Terminal display (1Hz) ---
                if now - last_display_time >= DISPLAY_RATE:
                    last_display_time = now

                    if on_track and player_idx is not None:
                        timing.update_sectors()
                        timing_data = timing.get_timing_data()
                        display_cache['timing_data'] = timing_data

                        # Update cached gaps for the data logger
                        if timing_data:
                            ca = timing_data.get('catch_ahead')
                            cb = timing_data.get('catch_behind')
                            cached_gap_ahead = ca.get('gap') if ca else None
                            cached_gap_behind = cb.get('gap') if cb else None

                        display_cache['weather_data'] = session.get_weather()
                        display_cache['session_info'] = session.get_session_info()

                        # Pit / fuel calculation
                        try:
                            lap_history = data_logger.get_lap_data() or []
                            fuel_used_history = []
                            for r in lap_history:
                                try:
                                    v = float(r.get('FuelUsed') or 0)
                                except (TypeError, ValueError):
                                    v = 0
                                if v > 0:
                                    fuel_used_history.append(v)
                            display_cache['pit_data'] = compute_pit_window(
                                fuel_level=conn.get('FuelLevel'),
                                fuel_use_per_hour=conn.get('FuelUsePerHour'),
                                last_lap_time=conn.get('LapLastLapTime') or
                                              (timing_data.get('player', {}) or {}).get('last_lap'),
                                lap_fuel_history=fuel_used_history,
                            )
                        except Exception:
                            display_cache['pit_data'] = None

                        # Tyre snapshot
                        try:
                            tdata = read_tires(conn)
                            display_cache['tire_data'] = tdata if tires_has_data(tdata) else None
                        except Exception:
                            display_cache['tire_data'] = None

                        if track_mapper.mapping_complete:
                            src = 'DB' if track_saved or (track_key and not track_mapper.track_points) else 'Live'
                            display_cache['map_status'] = f'Aktiv [{src}] ({track_mapper.point_count} Punkte)'
                        else:
                            display_cache['map_status'] = f'Erfasse... {track_mapper.coverage_pct}% Abdeckung'
                    else:
                        display_cache['map_status'] = 'Nicht auf der Strecke'

                    live.update(renderer.render(**display_cache))

                time.sleep(TICK_RATE)

    except KeyboardInterrupt:
        print("\nSession beendet. Erstelle Zusammenfassung...")
    finally:
        # Generate session summary before closing
        try:
            lap_data = data_logger.get_lap_data()
            if lap_data:
                summary_path = generate_session_summary(
                    session_dir, lap_data,
                    track_name=track_name,
                    session_type=session_type,
                )
                print(f"Session Summary: {summary_path}")
                print(f"Telemetry CSV:   {os.path.join(session_dir, 'telemetry_detailed.csv')}")
                print(f"Lap Summary:     {os.path.join(session_dir, 'lap_summary.csv')}")
                print(f"Position Graph:  {os.path.join(session_dir, 'position_graph.png')}")
            else:
                print("Keine Rundendaten fuer Summary vorhanden.")
        except Exception as e:
            print(f"Fehler beim Erstellen der Zusammenfassung: {e}")

        # Generate lap analysis plots
        try:
            print("Erstelle Rundenanalyse...")
            analysis_path = generate_lap_analysis(session_dir)
            if analysis_path:
                print(f"Lap Analysis:    {analysis_path}")
                print(f"Delta Analysis:  {os.path.join(session_dir, 'lap_delta_analysis.png')}")
            else:
                print("Nicht genug Daten fuer Rundenanalyse.")
        except Exception as e:
            print(f"Fehler bei Rundenanalyse: {e}")

        data_logger.close()
        map_window.stop()
        conn.shutdown()


if __name__ == "__main__":
    main()
