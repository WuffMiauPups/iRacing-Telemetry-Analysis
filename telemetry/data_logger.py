import csv
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import config


# All telemetry variables to log per tick (10Hz)
TICK_COLUMNS = [
    'Timestamp',
    'SessionTime',
    'Lap',
    'LapDistPct',
    'Speed_ms',
    'Speed_kmh',
    'Throttle',
    'Brake',
    'Clutch',
    'Gear',
    'RPM',
    'SteeringWheelAngle',
    'SteeringWheelTorque',
    'Position',
    'ClassPosition',
    'LapCurrentLapTime',
    'LastLapTime',
    'BestLapTime',
    'Gap_Ahead_s',
    'Gap_Behind_s',
    'Incidents',
    'FuelLevel',
    'FuelUsePerHour',
    'LatAccel',
    'LonAccel',
    'YawNorth',
    'Pitch',
    'Roll',
    'VelocityX',
    'VelocityY',
    'VelocityZ',
    'OilTemp',
    'OilPress',
    'WaterTemp',
    'Voltage',
    'SessionFlags',
    'OnPitRoad',
    'PlayerCarInPitStall',
    'LapDeltaToBestLap',
    'LapDeltaToBestLap_OK',
]

# Lap summary columns (one row per completed lap)
LAP_COLUMNS = [
    'Lap',
    'LapTime',
    'Position',
    'PositionChange',
    'Incidents',
    'FuelUsed',
    'AvgSpeed_kmh',
    'MaxSpeed_kmh',
    'AvgThrottle',
    'AvgBrake',
    'GearShifts',
]


@dataclass
class _LapAccumulator:
    """Per-lap aggregation state.

    Replaces a fistful of loose self._lap_* fields. Atomically reset
    via reset() so a partially-initialised lap can never leak data
    from the previous one.
    """
    start_fuel: Optional[float] = None
    start_pos: Optional[int] = None
    start_incidents: int = 0
    last_gear: Optional[int] = None
    gear_shifts: int = 0
    speeds: List[float] = field(default_factory=list)
    throttles: List[float] = field(default_factory=list)
    brakes: List[float] = field(default_factory=list)

    def reset(self, start_fuel, start_pos, start_incidents, start_gear):
        self.start_fuel = start_fuel
        self.start_pos = start_pos
        self.start_incidents = start_incidents
        self.last_gear = start_gear
        self.gear_shifts = 0
        self.speeds = []
        self.throttles = []
        self.brakes = []

    def record(self, speed_kmh, throttle, brake, gear):
        if speed_kmh is not None and speed_kmh > 3.6:  # > 1 m/s
            self.speeds.append(speed_kmh)
        if throttle is not None:
            self.throttles.append(throttle)
        if brake is not None:
            self.brakes.append(brake)
        if gear is not None and self.last_gear is not None and gear != self.last_gear:
            self.gear_shifts += 1
        self.last_gear = gear


class DataLogger:
    """Logs telemetry data to CSV files.

    Creates two CSV files per session:
    - telemetry_detailed.csv: High-frequency (10Hz) tick data
    - lap_summary.csv: One row per completed lap with aggregated stats

    Handles Practice mode quirks:
    - Position=0 means not classified (in pit/garage) — tracked but filtered in summary
    - Lap counter resets when returning to pit — uses continuous numbering
    - Incident counter can reset — clamps negative deltas to 0
    - Fuel can increase on reset/refuel — clamps negative usage to 0
    """

    def __init__(self, session_dir):
        self.session_dir = session_dir
        os.makedirs(session_dir, exist_ok=True)

        # Tick CSV
        self._tick_path = os.path.join(session_dir, 'telemetry_detailed.csv')
        self._tick_file = open(self._tick_path, 'w', newline='', encoding='utf-8')
        self._tick_writer = csv.DictWriter(self._tick_file, fieldnames=TICK_COLUMNS,
                                           extrasaction='ignore')
        self._tick_writer.writeheader()

        # Lap CSV
        self._lap_path = os.path.join(session_dir, 'lap_summary.csv')
        self._lap_file = open(self._lap_path, 'w', newline='', encoding='utf-8')
        self._lap_writer = csv.DictWriter(self._lap_file, fieldnames=LAP_COLUMNS,
                                          extrasaction='ignore')
        self._lap_writer.writeheader()

        # Continuous lap counter (doesn't reset like iRacing's CarIdxLap)
        self._continuous_lap = 0
        self._iracing_lap = None  # Last seen CarIdxLap value

        # Per-lap aggregation state (atomic via dataclass)
        self._lap = _LapAccumulator()
        self._last_valid_position = None  # Last position > 0

        self._tick_count = 0
        self._start_time = time.time()

    def log_tick(self, conn, player_idx, gap_ahead=None, gap_behind=None):
        """Log one telemetry tick. Call at 10Hz."""
        if player_idx is None:
            return

        now = time.time()
        elapsed = now - self._start_time

        # Read all telemetry values
        speed = conn.get('Speed') or 0
        throttle = conn.get('Throttle')
        brake = conn.get('Brake')
        gear = conn.get('Gear')
        rpm = conn.get('RPM')

        laps = conn.get('CarIdxLap')
        iracing_lap = laps[player_idx] if laps else None
        positions = conn.get('CarIdxPosition')
        position = positions[player_idx] if positions else None
        pcts = conn.get('CarIdxLapDistPct')
        pct = pcts[player_idx] if pcts else None
        last_lap_times = conn.get('CarIdxLastLapTime')
        last_lap = last_lap_times[player_idx] if last_lap_times else None
        best_lap_times = conn.get('CarIdxBestLapTime')
        best_lap = best_lap_times[player_idx] if best_lap_times else None
        incidents = conn.get('PlayerCarMyIncidentCount') or 0

        # Track last valid position (> 0 means actually racing/classified)
        if position is not None and position > 0:
            self._last_valid_position = position

        row = {
            'Timestamp': round(elapsed, 2),
            'SessionTime': conn.get('SessionTime'),
            'Lap': self._continuous_lap,
            'LapDistPct': round(pct, 5) if pct is not None else None,
            'Speed_ms': round(speed, 2),
            'Speed_kmh': round(speed * 3.6, 1),
            'Throttle': round(throttle, 3) if throttle is not None else None,
            'Brake': round(brake, 3) if brake is not None else None,
            'Clutch': round(conn.get('Clutch') or 0, 3),
            'Gear': gear,
            'RPM': round(rpm, 0) if rpm is not None else None,
            'SteeringWheelAngle': round(conn.get('SteeringWheelAngle') or 0, 4),
            'SteeringWheelTorque': round(conn.get('SteeringWheelTorque') or 0, 4),
            'Position': position if position and position > 0 else self._last_valid_position,
            'ClassPosition': conn.get('PlayerCarClassPosition'),
            'LapCurrentLapTime': conn.get('LapCurrentLapTime'),
            'LastLapTime': last_lap if last_lap and last_lap > 0 else None,
            'BestLapTime': best_lap if best_lap and best_lap > 0 else None,
            'Gap_Ahead_s': round(gap_ahead, 3) if gap_ahead is not None else None,
            'Gap_Behind_s': round(gap_behind, 3) if gap_behind is not None else None,
            'Incidents': incidents,
            'FuelLevel': round(conn.get('FuelLevel') or 0, 3),
            'FuelUsePerHour': round(conn.get('FuelUsePerHour') or 0, 3),
            'LatAccel': round(conn.get('LatAccel') or 0, 3),
            'LonAccel': round(conn.get('LonAccel') or 0, 3),
            'YawNorth': round(conn.get('YawNorth') or 0, 4),
            'Pitch': round(conn.get('Pitch') or 0, 4),
            'Roll': round(conn.get('Roll') or 0, 4),
            'VelocityX': round(conn.get('VelocityX') or 0, 3),
            'VelocityY': round(conn.get('VelocityY') or 0, 3),
            'VelocityZ': round(conn.get('VelocityZ') or 0, 3),
            'OilTemp': conn.get('OilTemp'),
            'OilPress': conn.get('OilPress'),
            'WaterTemp': conn.get('WaterTemp'),
            'Voltage': conn.get('Voltage'),
            'SessionFlags': conn.get('SessionFlags'),
            'OnPitRoad': conn.get('OnPitRoad'),
            'PlayerCarInPitStall': conn.get('PlayerCarInPitStall'),
            'LapDeltaToBestLap': conn.get('LapDeltaToBestLap'),
            'LapDeltaToBestLap_OK': conn.get('LapDeltaToBestLap_OK'),
        }

        self._tick_writer.writerow(row)
        self._tick_count += 1

        if self._tick_count % config.TICK_FLUSH_INTERVAL == 0:
            self._tick_file.flush()

        # --- Detect new lap (handles iRacing lap counter resets) ---
        if iracing_lap is not None and iracing_lap != self._iracing_lap:
            if self._iracing_lap is not None:
                # Lap changed — finalize previous lap
                # Use the best position we saw (last_valid_position), not 0
                effective_pos = self._last_valid_position or position
                self._finalize_lap(effective_pos, incidents,
                                   conn.get('FuelLevel'), last_lap)
                self._continuous_lap += 1

            # Start tracking new lap
            self._iracing_lap = iracing_lap
            self._lap.reset(
                start_fuel=conn.get('FuelLevel'),
                start_pos=self._last_valid_position or position,
                start_incidents=incidents,
                start_gear=gear,
            )

        # Accumulate per-lap stats
        self._lap.record(speed * 3.6 if speed else None, throttle, brake, gear)

    def _finalize_lap(self, current_pos, current_incidents, current_fuel, lap_time):
        """Write a completed lap to the lap summary CSV."""
        lap = self._lap

        # Position change (positive = gained positions)
        pos_change = 0
        start_pos = lap.start_pos
        if start_pos and start_pos > 0 and current_pos and current_pos > 0:
            pos_change = start_pos - current_pos

        # Fuel used — clamp to 0 if negative (refueled/reset)
        fuel_used = 0
        if lap.start_fuel is not None and current_fuel is not None:
            fuel_used = lap.start_fuel - current_fuel
            if fuel_used < 0:
                fuel_used = 0  # Refueled — don't show negative

        # Incidents — clamp to 0 if negative (counter reset)
        incidents_this_lap = 0
        if current_incidents is not None:
            incidents_this_lap = current_incidents - lap.start_incidents
            if incidents_this_lap < 0:
                incidents_this_lap = 0  # Counter reset

        avg_speed = sum(lap.speeds) / len(lap.speeds) if lap.speeds else 0
        max_speed = max(lap.speeds) if lap.speeds else 0
        avg_throttle = sum(lap.throttles) / len(lap.throttles) if lap.throttles else 0
        avg_brake = sum(lap.brakes) / len(lap.brakes) if lap.brakes else 0

        # Use last valid position if current is 0/None
        display_pos = current_pos if current_pos and current_pos > 0 else self._last_valid_position

        row = {
            'Lap': self._continuous_lap + 1,  # 1-based for display
            'LapTime': round(lap_time, 3) if lap_time and lap_time > 0 else None,
            'Position': display_pos if display_pos and display_pos > 0 else None,
            'PositionChange': pos_change,
            'Incidents': incidents_this_lap,
            'FuelUsed': round(fuel_used, 3),
            'AvgSpeed_kmh': round(avg_speed, 1),
            'MaxSpeed_kmh': round(max_speed, 1),
            'AvgThrottle': round(avg_throttle * 100, 1),
            'AvgBrake': round(avg_brake * 100, 1),
            'GearShifts': lap.gear_shifts,
        }

        self._lap_writer.writerow(row)
        self._lap_file.flush()

    def get_lap_data(self):
        """Read back the lap summary data for session summary generation."""
        self._lap_file.flush()
        laps = []
        try:
            with open(self._lap_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    laps.append(row)
        except Exception:
            pass
        return laps

    def close(self):
        """Close CSV files."""
        try:
            self._tick_file.flush()
            self._tick_file.close()
        except Exception:
            pass
        try:
            self._lap_file.flush()
            self._lap_file.close()
        except Exception:
            pass
