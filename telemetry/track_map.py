import math
import time

import config
from telemetry.track_db import save_track, load_track


class TrackMapper:
    """Builds the track shape by integrating Speed + YawNorth during the
    first full lap, then maps any car's LapDistPct to screen coords.

    Uses Speed + YawNorth (NOT VelocityX/Z which are car-local).

    Completion check: requires LapDistPct coverage > 95% of the track,
    NOT based on lap counter (which fires too early when exiting pits).
    """

    def __init__(self, sample_interval=None):
        self.sample_interval = sample_interval if sample_interval is not None else config.TRACK_SAMPLE_INTERVAL
        self.track_points = []     # (pct, world_x, world_z)
        self._sampled_pcts = set() # Track which 1% buckets we've covered
        self.mapping_complete = False
        self._normalized_points = []
        self._track_key = None

        # World position state
        self._world_x = 0.0
        self._world_z = 0.0
        self._last_time = None
        self._last_pct = None

        # Pre-baked lookup table built after mapping completes / loads
        self._lut = None  # list of (x, y) at TRACK_LUT_BUCKETS evenly spaced pct

    def record_tick(self, lap_dist_pct, speed, yaw_north):
        """Record a position tick during the mapping lap.

        Args:
            lap_dist_pct: 0.0-1.0 position around the track
            speed: Car speed in m/s
            yaw_north: Car heading relative to north in radians
        """
        if self.mapping_complete:
            return

        if speed is None or yaw_north is None or lap_dist_pct is None:
            return

        now = time.time()

        if self._last_time is None:
            self._last_time = now
            self._last_pct = lap_dist_pct
            return

        dt = now - self._last_time
        self._last_time = now

        # Skip bad time deltas. After dropping a sample we must NOT integrate
        # the next dt against the now-stale yaw — reset the timing reference
        # so the integrator restarts cleanly on the next valid tick.
        if dt <= 0 or dt > 0.5:
            self._last_time = None
            self._last_pct = None
            return

        # Integrate world position when moving
        if speed > 1.0:
            self._world_x += speed * math.sin(yaw_north) * dt
            self._world_z += speed * math.cos(yaw_north) * dt

        # Sample at regular LapDistPct intervals
        if self._last_pct is None:
            self._last_pct = lap_dist_pct

        # Handle wraparound (0.99 -> 0.01)
        delta_pct = lap_dist_pct - self._last_pct
        if delta_pct < -0.5:
            delta_pct += 1.0  # Wrapped around
        elif delta_pct < 0:
            delta_pct = abs(delta_pct)  # Small backward movement

        if delta_pct >= self.sample_interval and speed > 1.0:
            self.track_points.append((lap_dist_pct, self._world_x, self._world_z))
            self._last_pct = lap_dist_pct

            # Track coverage in 1% buckets
            bucket = int(lap_dist_pct * 100)
            self._sampled_pcts.add(bucket)

    def check_coverage(self):
        """Check if we've covered enough of the track to finish mapping.

        Returns coverage as 0.0-1.0.
        """
        # Need at least 95 of the 100 buckets covered
        return len(self._sampled_pcts) / 100.0

    def try_finish_mapping(self):
        """Try to finish mapping if coverage is sufficient.

        Returns True if mapping is now complete.
        """
        if self.mapping_complete:
            return True

        coverage = self.check_coverage()
        if (coverage >= config.TRACK_COVERAGE_THRESHOLD and
                len(self.track_points) >= config.TRACK_MIN_POINTS_FOR_FINISH):
            return self.finish_mapping()
        return False

    def finish_mapping(self):
        """Normalize recorded coordinates to 0-1 range for rendering."""
        if len(self.track_points) < config.TRACK_MIN_POINTS_FOR_NORMALIZE:
            return False

        self.track_points.sort(key=lambda p: p[0])

        xs = [p[1] for p in self.track_points]
        zs = [p[2] for p in self.track_points]

        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)

        x_range = max_x - min_x or 1e-6
        z_range = max_z - min_z or 1e-6

        # Preserve aspect ratio using the larger dimension
        scale = max(x_range, z_range)

        x_offset = (scale - x_range) / 2
        z_offset = (scale - z_range) / 2

        self._normalized_points = []
        for pct, x, z in self.track_points:
            nx = (x - min_x + x_offset) / scale
            ny = 1.0 - (z - min_z + z_offset) / scale  # Flip Y
            self._normalized_points.append((pct, nx, ny))

        self.mapping_complete = True
        self._build_lut()
        return True

    def _build_lut(self):
        """Pre-compute a fixed-resolution lookup table for get_position.

        Once mapping is done the layout never changes. We pre-compute
        TRACK_LUT_BUCKETS evenly-spaced positions so per-tick lookups
        become O(1) instead of O(log N) per car. The exact same numerical
        result as the original interpolating get_position() at bucket
        boundaries, with negligible error in between.
        """
        if not self._normalized_points:
            self._lut = None
            return
        n = config.TRACK_LUT_BUCKETS
        lut = []
        for i in range(n):
            pct = i / n
            lut.append(self._interpolate_position(pct))
        self._lut = lut

    def get_track_outline(self):
        """Get normalized track outline as list of (x, y) tuples."""
        return [(x, y) for _, x, y in self._normalized_points]

    def get_position(self, lap_dist_pct):
        """Interpolate a car's position from its LapDistPct.

        Fast path: pre-computed LUT (O(1)). Falls back to interpolation
        if the LUT was not built (e.g. legacy code paths).
        """
        if not self.mapping_complete or not self._normalized_points:
            return None
        if lap_dist_pct is None:
            return None

        pct = lap_dist_pct % 1.0

        if self._lut is not None:
            n = len(self._lut)
            idx = int(pct * n)
            if idx >= n:
                idx = n - 1
            return self._lut[idx]

        return self._interpolate_position(pct)

    def _interpolate_position(self, pct):
        """Original O(log N) interpolation. Used to seed the LUT."""
        points = self._normalized_points
        if not points:
            return None

        # Handle pct outside recorded range (wraparound)
        if pct <= points[0][0] or pct >= points[-1][0]:
            p0_pct, p0_x, p0_y = points[-1]
            p1_pct, p1_x, p1_y = points[0]
            span = (1.0 - p0_pct) + p1_pct
            if span > 0:
                if pct >= p0_pct:
                    t = (pct - p0_pct) / span
                else:
                    t = ((1.0 - p0_pct) + pct) / span
                return (p0_x + t * (p1_x - p0_x), p0_y + t * (p1_y - p0_y))
            return (p0_x, p0_y)

        # Binary search for surrounding points
        lo, hi = 0, len(points) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if points[mid][0] <= pct:
                lo = mid
            else:
                hi = mid

        p0_pct, p0_x, p0_y = points[lo]
        p1_pct, p1_x, p1_y = points[hi]

        span = p1_pct - p0_pct
        if span > 0:
            t = max(0.0, min(1.0, (pct - p0_pct) / span))
            return (p0_x + t * (p1_x - p0_x), p0_y + t * (p1_y - p0_y))

        return (p0_x, p0_y)

    def load_from_db(self, track_key):
        """Try to load a saved track layout from the database.

        Returns True if loaded successfully (no mapping lap needed).
        """
        if track_key is None:
            return False

        points = load_track(track_key)
        if points is None:
            return False

        self._normalized_points = points
        self.mapping_complete = True
        self._track_key = track_key
        self._build_lut()
        return True

    def save_to_db(self, track_key):
        """Save the current track layout to the database."""
        if not self.mapping_complete or not self._normalized_points:
            return None

        self._track_key = track_key
        return save_track(track_key, self._normalized_points)

    @property
    def point_count(self):
        return len(self.track_points) or len(self._normalized_points)

    @property
    def coverage_pct(self):
        return len(self._sampled_pcts)
