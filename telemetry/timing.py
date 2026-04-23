import os
import time

import config


# Default sector splits (3 equal sectors) — re-exported for backward compat.
DEFAULT_SECTOR_SPLITS = config.DEFAULT_SECTOR_SPLITS


def _safe_idx(arr, i):
    """Return arr[i] if the index is valid, else None.

    Defensive helper for the iRacing CarIdx* arrays. The arrays are normally
    long enough, but during session changes / connection re-init they can be
    shorter than expected, return None entries, or briefly be wrong types.
    """
    if arr is None:
        return None
    try:
        v = arr[i]
    except (IndexError, TypeError):
        return None
    return v


class SectorTracker:
    """Tracks sector times for all cars by monitoring CarIdxLapDistPct."""

    def __init__(self, sector_splits=None):
        self.sector_splits = sector_splits or DEFAULT_SECTOR_SPLITS
        self.num_sectors = len(self.sector_splits)
        # Per car_idx tracking state
        self._tracking = {}

    def _init_car(self, car_idx):
        """Initialize tracking for a car."""
        self._tracking[car_idx] = {
            'last_pct': 0.0,
            'current_sector': 0,
            'sector_start_time': time.time(),
            'completed_sectors': [],
            'last_lap_sectors': [],
        }

    def _get_sector_index(self, pct):
        """Determine which sector a track percentage falls into."""
        for i, boundary in enumerate(self.sector_splits):
            if pct < boundary:
                return i
        return self.num_sectors - 1

    def update(self, car_idx, lap_dist_pct, lap_num):
        """Update sector tracking for a car.

        Call this each tick with the car's current lap distance percentage.
        """
        if car_idx not in self._tracking:
            self._init_car(car_idx)

        state = self._tracking[car_idx]
        now = time.time()
        current_sector = self._get_sector_index(lap_dist_pct)

        # Detect sector boundary crossing
        if current_sector != state['current_sector']:
            # Check for forward progression (not a teleport/reset)
            if current_sector == (state['current_sector'] + 1) % self.num_sectors:
                sector_time = now - state['sector_start_time']
                state['completed_sectors'].append(sector_time)
                state['sector_start_time'] = now

                # If we completed all sectors (crossed start/finish)
                if current_sector == 0:
                    state['last_lap_sectors'] = state['completed_sectors']
                    state['completed_sectors'] = []
            else:
                # Non-sequential sector change (pit, reset, etc.) — reset tracking
                state['completed_sectors'] = []
                state['sector_start_time'] = now

            state['current_sector'] = current_sector

        state['last_pct'] = lap_dist_pct

    def get_last_lap_sectors(self, car_idx):
        """Get completed sector times for the last full lap."""
        if car_idx in self._tracking:
            return self._tracking[car_idx]['last_lap_sectors']
        return []

    def get_current_sectors(self, car_idx):
        """Get sector times completed so far in the current lap."""
        if car_idx in self._tracking:
            return self._tracking[car_idx]['completed_sectors']
        return []


class CatchCalculator:
    """Calculates time to catch car ahead / time to be caught by car behind.

    Pace estimation uses Exponential Moving Average (EMA):
        P_n = alpha * L_n + (1 - alpha) * P_(n-1)

    Where:
        P_n   = new pace estimate after lap n
        L_n   = lap time of lap n (latest)
        alpha = smoothing factor (0-1), higher = more reactive

    Plus live gap trend from CarIdxEstTime for mid-lap reactivity.

    Final delta = (lap_weight * ema_delta) + (live_weight * live_delta)
    """

    def __init__(self, alpha=None, lap_weight=None, live_weight=None):
        self.alpha = alpha if alpha is not None else config.EMA_ALPHA
        self.lap_weight = lap_weight if lap_weight is not None else config.LAP_WEIGHT
        self.live_weight = live_weight if live_weight is not None else config.LIVE_WEIGHT

        self._ema_pace = {}           # car_idx -> current EMA pace (single float)
        self._last_lap_num = {}       # car_idx -> last seen lap number
        self._lap_count = {}          # car_idx -> number of laps recorded

        # Live gap tracking: (timestamp, gap) per car pair
        self._gap_history = {}
        self._gap_history_max = config.GAP_HISTORY_MAX

    def record_lap(self, car_idx, lap_num, lap_time):
        """Record a completed lap time and update the EMA pace.

        P_n = alpha * L_n + (1 - alpha) * P_(n-1)
        First lap: P_0 = L_0 (no history to smooth against)
        """
        if lap_time is None or lap_time <= 0:
            return

        last = self._last_lap_num.get(car_idx)
        if last is not None and lap_num == last:
            return

        self._last_lap_num[car_idx] = lap_num

        # Filter obviously broken laps BEFORE updating EMA.
        # Some sessions emit tiny/invalid lap times (pit reset, teleport, timing glitches).
        if lap_time < config.LAP_TIME_FILTER_MIN_S:
            return

        # Keep the existing slow-lap filter, and add a "too fast to be real" guard.
        if car_idx in self._ema_pace:
            ema = self._ema_pace[car_idx]
            if lap_time > ema * config.LAP_TIME_FILTER_MAX_FACTOR:
                return  # Discard — don't let it poison the pace estimate
            if lap_time < ema * config.LAP_TIME_FILTER_MIN_FACTOR:
                return

        if car_idx not in self._ema_pace:
            # First lap — initialize EMA
            self._ema_pace[car_idx] = lap_time
            self._lap_count[car_idx] = 1
        else:
            # EMA update: P_n = alpha * L_n + (1 - alpha) * P_(n-1)
            self._ema_pace[car_idx] = (
                self.alpha * lap_time +
                (1 - self.alpha) * self._ema_pace[car_idx]
            )
            self._lap_count[car_idx] += 1

    def get_pace(self, car_idx):
        """Get the current EMA pace estimate for a car."""
        return self._ema_pace.get(car_idx)

    def record_gap(self, player_idx, other_idx, gap_seconds):
        """Record a live gap measurement for trend calculation."""
        if gap_seconds is None or gap_seconds <= 0:
            return

        key = (player_idx, other_idx)
        now = time.time()

        if key not in self._gap_history:
            self._gap_history[key] = []

        # Reject impossible one-sample spikes (commonly caused by S/F wrap in EstTime).
        if self._gap_history[key]:
            prev_t, prev_gap = self._gap_history[key][-1]
            dt = now - prev_t
            if 0 < dt <= 2.0:
                gap_rate = abs(gap_seconds - prev_gap) / dt
                if gap_rate > config.GAP_RATE_MAX:
                    return

        self._gap_history[key].append((now, gap_seconds))

        if len(self._gap_history[key]) > self._gap_history_max:
            self._gap_history[key] = self._gap_history[key][-self._gap_history_max:]

    def get_live_delta_per_second(self, player_idx, other_idx):
        """How fast the gap is changing (seconds/second).

        Uses a trimmed average of per-sample gap rates over the last 30s.
        Positive = gap shrinking (catching). Negative = gap growing.
        """
        key = (player_idx, other_idx)
        samples = self._gap_history.get(key)
        if not samples or len(samples) < 3:
            return None

        cutoff = time.time() - config.GAP_HISTORY_WINDOW_S
        recent = [(t, g) for t, g in samples if t >= cutoff]
        if len(recent) < 3:
            return None

        rates = []
        for i in range(1, len(recent)):
            t0, g0 = recent[i - 1]
            t1, g1 = recent[i]
            dt = t1 - t0
            if dt <= 0:
                continue

            # Positive => catching (gap shrinking)
            rate = (g0 - g1) / dt

            # Ignore physically implausible rates from telemetry spikes.
            if abs(rate) > config.GAP_RATE_MAX:
                continue

            rates.append(rate)

        if len(rates) < 2:
            return None

        rates.sort()
        trim = int(len(rates) * 0.2)
        if trim > 0 and len(rates) > (trim * 2):
            rates = rates[trim:-trim]

        return sum(rates) / len(rates)

    def calc_catch_time(self, gap_seconds, player_idx, other_idx, my_pace, other_pace):
        """Calculate catch time using blended EMA pace + live gap trend.

        Returns dict with gap info, or None if insufficient data.
        """
        if gap_seconds is None or gap_seconds <= 0:
            return None

        # Source 1: EMA pace delta
        ema_delta_per_lap = None
        if my_pace is not None and other_pace is not None:
            ema_delta_per_lap = other_pace - my_pace  # positive = I'm faster

        # Source 2: Live gap trend
        live_delta_per_sec = self.get_live_delta_per_second(player_idx, other_idx)

        live_delta_per_lap = None
        if live_delta_per_sec is not None and my_pace is not None and my_pace > 0:
            live_delta_per_lap = live_delta_per_sec * my_pace

        # Blend
        if ema_delta_per_lap is not None and live_delta_per_lap is not None:
            blended = (self.lap_weight * ema_delta_per_lap +
                       self.live_weight * live_delta_per_lap)
        elif ema_delta_per_lap is not None:
            blended = ema_delta_per_lap
        elif live_delta_per_lap is not None:
            blended = live_delta_per_lap
        else:
            return {
                'laps_to_catch': None, 'seconds_to_catch': None,
                'gaining': False, 'per_lap_delta': 0.0,
                'live_delta_per_sec': None, 'gap': round(gap_seconds, 2),
            }

        # Safety clamp against rare outliers so the UI doesn't show nonsense
        # like +/- 80s per lap due to a single bad telemetry sample.
        blended = max(-config.DELTA_CLAMP, min(config.DELTA_CLAMP, blended))

        if abs(blended) < config.DELTA_DEADBAND:
            return {
                'laps_to_catch': None, 'seconds_to_catch': None,
                'gaining': False, 'per_lap_delta': round(blended, 3),
                'live_delta_per_sec': round(live_delta_per_sec, 4) if live_delta_per_sec else None,
                'gap': round(gap_seconds, 2),
            }

        laps_to_catch = gap_seconds / blended
        ref_pace = my_pace if my_pace and my_pace > 0 else 90
        seconds_to_catch = laps_to_catch * ref_pace if laps_to_catch > 0 else None

        return {
            'laps_to_catch': round(laps_to_catch, 1) if laps_to_catch > 0 else None,
            'seconds_to_catch': round(seconds_to_catch, 0) if seconds_to_catch and seconds_to_catch > 0 else None,
            'gaining': blended > 0,
            'per_lap_delta': round(blended, 3),
            'live_delta_per_sec': round(live_delta_per_sec, 4) if live_delta_per_sec else None,
            'gap': round(gap_seconds, 2),
        }


class TimingMonitor:
    """Reads lap times and positions, tracks cars ahead and behind."""

    def __init__(self, connection, sector_splits=None):
        self.conn = connection
        self.sector_tracker = SectorTracker(sector_splits)
        self.catch_calc = CatchCalculator()
        gap_mode = os.getenv('IRACING_GAP_MODE', config.GAP_MODE_DEFAULT).strip().lower()
        if gap_mode not in config.GAP_MODES_VALID:
            gap_mode = config.GAP_MODE_DEFAULT
        self._gap_mode = gap_mode

    def get_player_idx(self):
        return self.conn.get('PlayerCarIdx')

    def get_positions(self):
        """Return CarIdxPosition array."""
        return self.conn.get('CarIdxPosition')

    def find_car_at_position(self, positions, target_pos):
        """Find car_idx that has the given race position."""
        if positions is None:
            return None
        for car_idx, pos in enumerate(positions):
            if pos == target_pos and pos > 0:
                return car_idx
        return None

    def get_driver_name(self, car_idx):
        """Get driver name from cached DriverInfo."""
        try:
            drivers = self.conn.driver_info['Drivers']
            for d in drivers:
                if d['CarIdx'] == car_idx:
                    return d.get('UserName', f'#{car_idx}')
            return f'#{car_idx}'
        except (TypeError, KeyError):
            return f'#{car_idx}'

    def get_car_number(self, car_idx):
        """Get car number from cached DriverInfo."""
        try:
            drivers = self.conn.driver_info['Drivers']
            for d in drivers:
                if d['CarIdx'] == car_idx:
                    return d.get('CarNumber', '?')
            return '?'
        except (TypeError, KeyError):
            return '?'

    def update_sectors(self):
        """Update sector tracking for all relevant cars."""
        lap_dist_pcts = self.conn.get('CarIdxLapDistPct')
        laps = self.conn.get('CarIdxLap')
        if lap_dist_pcts is None or laps is None:
            return

        positions = self.get_positions()
        if positions is None:
            return

        for car_idx, pos in enumerate(positions):
            if pos is None or pos <= 0:
                continue
            pct = _safe_idx(lap_dist_pcts, car_idx)
            lap = _safe_idx(laps, car_idx)
            if pct is None or lap is None:
                continue
            self.sector_tracker.update(car_idx, pct, lap)

    def update_catch_calculator(self):
        """Feed latest lap times into the catch calculator for all cars."""
        last_lap_times = self.conn.get('CarIdxLastLapTime')
        laps = self.conn.get('CarIdxLap')
        positions = self.get_positions()

        if last_lap_times is None or laps is None or positions is None:
            return

        for car_idx, pos in enumerate(positions):
            if pos is None or pos <= 0:
                continue
            llt = _safe_idx(last_lap_times, car_idx)
            lap = _safe_idx(laps, car_idx)
            if llt is None or lap is None or llt <= 0:
                continue
            self.catch_calc.record_lap(car_idx, lap, llt)

    def _get_gap_to_car(self, player_idx, other_idx):
        """Estimate gap in seconds between two cars.

        Uses lap progress as the primary source (stable across start/finish wrap),
        then blends with CarIdxEstTime only if both sources roughly agree.
        """
        if self._gap_mode == 'legacy':
            return self._get_gap_to_car_legacy(player_idx, other_idx)

        pcts = self.conn.get('CarIdxLapDistPct')
        laps = self.conn.get('CarIdxLap')
        last_times = self.conn.get('CarIdxLastLapTime')
        best_times = self.conn.get('CarIdxBestLapTime')

        my_pct = _safe_idx(pcts, player_idx)
        other_pct = _safe_idx(pcts, other_idx)
        my_lap = _safe_idx(laps, player_idx)
        other_lap = _safe_idx(laps, other_idx)

        gap_progress = None
        if (my_pct is not None and other_pct is not None and
                my_lap is not None and other_lap is not None and
                my_pct >= 0 and other_pct >= 0):

            my_last = _safe_idx(last_times, player_idx)
            my_best = _safe_idx(best_times, player_idx)
            ref_time = None
            if my_last is not None and my_last > 0:
                ref_time = my_last
            elif my_best is not None and my_best > 0:
                ref_time = my_best

            if ref_time is not None:
                pct_diff = (other_lap + other_pct) - (my_lap + my_pct)
                gap_progress = abs(pct_diff * ref_time)

        gap_est = None
        est_times = self.conn.get('CarIdxEstTime')
        my_est = _safe_idx(est_times, player_idx)
        other_est = _safe_idx(est_times, other_idx)
        if (my_est is not None and other_est is not None and
                my_est > 0 and other_est > 0):
            gap_est = abs(other_est - my_est)

        if self._gap_mode == 'progress':
            return gap_progress

        if gap_progress is not None and gap_est is not None:
            # CarIdxEstTime can jump by ~1 lap near the line; trust progress on mismatch.
            if abs(gap_est - gap_progress) > max(4.0, gap_progress * 0.6):
                return gap_progress
            return (0.6 * gap_progress) + (0.4 * gap_est)

        if gap_progress is not None:
            return gap_progress
        return gap_est

    def _get_gap_to_car_legacy(self, player_idx, other_idx):
        """Previous gap estimator kept as fallback for safe rollout."""
        # Try iRacing's estimated time first
        est_times = self.conn.get('CarIdxEstTime')
        my_est = _safe_idx(est_times, player_idx)
        other_est = _safe_idx(est_times, other_idx)
        if (my_est is not None and other_est is not None and
                my_est > 0 and other_est > 0):
            gap = abs(other_est - my_est)
            if gap > 0:
                return gap

        # Fallback: use lap dist pct difference × last lap time
        pcts = self.conn.get('CarIdxLapDistPct')
        laps = self.conn.get('CarIdxLap')
        last_times = self.conn.get('CarIdxLastLapTime')

        my_pct = _safe_idx(pcts, player_idx)
        other_pct = _safe_idx(pcts, other_idx)
        my_lap = _safe_idx(laps, player_idx)
        other_lap = _safe_idx(laps, other_idx)
        ref_time = _safe_idx(last_times, player_idx)

        if (my_pct is None or other_pct is None or
                my_lap is None or other_lap is None or
                ref_time is None or ref_time <= 0):
            return None

        # Calculate distance difference considering lap difference
        pct_diff = (other_lap + other_pct) - (my_lap + my_pct)
        return abs(pct_diff * ref_time)

    def get_timing_data(self):
        """Get full timing data: player, car ahead, car behind, plus catch info."""
        player_idx = self.get_player_idx()
        if player_idx is None:
            return None

        positions = self.get_positions()
        if positions is None:
            return None

        my_pos = _safe_idx(positions, player_idx)
        if my_pos is None or my_pos == 0:
            return None

        last_lap_times = self.conn.get('CarIdxLastLapTime')
        best_lap_times = self.conn.get('CarIdxBestLapTime')

        # Update catch calculator with latest data
        self.update_catch_calculator()

        def build_entry(car_idx, label):
            if car_idx is None:
                return None
            last_lap = _safe_idx(last_lap_times, car_idx)
            if last_lap is None or last_lap <= 0:
                last_lap = None
            best_lap = _safe_idx(best_lap_times, car_idx)
            if best_lap is None or best_lap <= 0:
                best_lap = None
            return {
                'label': label,
                'car_idx': car_idx,
                'car_number': self.get_car_number(car_idx),
                'driver_name': self.get_driver_name(car_idx),
                'position': _safe_idx(positions, car_idx),
                'last_lap': last_lap,
                'best_lap': best_lap,
                'sectors': self.sector_tracker.get_last_lap_sectors(car_idx),
            }

        ahead_idx = self.find_car_at_position(positions, my_pos - 1) if my_pos > 1 else None
        behind_idx = self.find_car_at_position(positions, my_pos + 1)

        # Feed live gap data for real-time trend tracking
        if ahead_idx is not None:
            gap_ahead = self._get_gap_to_car(player_idx, ahead_idx)
            if gap_ahead is not None:
                self.catch_calc.record_gap(player_idx, ahead_idx, gap_ahead)
        else:
            gap_ahead = None

        if behind_idx is not None:
            gap_behind = self._get_gap_to_car(player_idx, behind_idx)
            if gap_behind is not None:
                self.catch_calc.record_gap(behind_idx, player_idx, gap_behind)
        else:
            gap_behind = None

        # Calculate catch data using blended pace
        my_pace = self.catch_calc.get_pace(player_idx)
        catch_ahead = None
        catch_behind = None

        if ahead_idx is not None:
            ahead_pace = self.catch_calc.get_pace(ahead_idx)
            catch_ahead = self.catch_calc.calc_catch_time(
                gap_ahead, player_idx, ahead_idx, my_pace, ahead_pace
            )

        if behind_idx is not None:
            behind_pace = self.catch_calc.get_pace(behind_idx)
            # Flip perspective: the car behind is trying to catch ME
            if gap_behind is not None and behind_pace is not None and my_pace is not None:
                catch_behind = self.catch_calc.calc_catch_time(
                    gap_behind, behind_idx, player_idx, behind_pace, my_pace
                )

        return {
            'ahead': build_entry(ahead_idx, 'FAHRER VOR MIR'),
            'player': build_entry(player_idx, 'ICH'),
            'behind': build_entry(behind_idx, 'FAHRER HINTER MIR'),
            'catch_ahead': catch_ahead,
            'catch_behind': catch_behind,
        }
