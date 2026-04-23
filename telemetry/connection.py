import irsdk
import time
import struct

import config


class IRacingConnection:
    """Manages connection to iRacing via shared memory.

    Uses direct shared memory reads (bypassing pyirsdk's freeze/snapshot)
    for tire data that was returning stale values.
    """

    def __init__(self):
        self.ir = irsdk.IRSDK()
        self.connected = False
        self._driver_info = None
        self._weekend_info = None
        self._session_info = None
        self._last_reinit = 0
        self._reinit_interval = config.REINIT_INTERVAL_S
        # Tick stagnation detection: if SessionTick stops advancing, treat
        # as a hard disconnect even if pyirsdk still claims is_connected.
        self._last_tick_value = None
        self._last_tick_change_time = 0.0
        self._tick_stagnation_s = config.TICK_STAGNATION_S

    def connect(self):
        """Connect to iRacing, blocks until iRacing is running."""
        while not self.ir.startup():
            print("Warte auf iRacing...")
            time.sleep(2)
        self.connected = True
        self._last_reinit = time.time()
        self._cache_session_data()

    def check_connection(self):
        """Return True if connected, False otherwise. Non-blocking.

        On hard disconnect (pyirsdk handle dead OR SessionTick stagnated),
        attempts a single non-blocking `ir.startup()`. The caller is
        responsible for deciding whether to keep polling or bail out —
        this method never spins on a blocking reconnect loop.
        """
        # Hard disconnect via pyirsdk
        hard_disconnect = not self.ir.is_connected

        # Soft disconnect: SessionTick stopped advancing for too long.
        # iRacing publishes a monotonically increasing tick. A stuck tick
        # means the sim crashed/froze even if the shared mem handle is alive.
        if not hard_disconnect:
            tick = self.get('SessionTick')
            now = time.time()
            if tick is not None:
                if tick != self._last_tick_value:
                    self._last_tick_value = tick
                    self._last_tick_change_time = now
                elif self._last_tick_change_time and \
                        (now - self._last_tick_change_time) > self._tick_stagnation_s:
                    hard_disconnect = True
            else:
                # Tick unreadable -> behave like hard disconnect once enough
                # time has passed since last successful read
                if self._last_tick_change_time and \
                        (now - self._last_tick_change_time) > self._tick_stagnation_s:
                    hard_disconnect = True

        if hard_disconnect:
            self._reset_yaml_cache()
            self.connected = False
            self._last_tick_value = None
            self._last_tick_change_time = 0.0
            try:
                self.ir.shutdown()
            except Exception:
                pass
            # Single non-blocking startup attempt. The worker loops back
            # on False; no spinning-sleep-print loop here.
            try:
                if self.ir.startup():
                    self.connected = True
                    self._last_reinit = time.time()
                    self._cache_session_data()
                    return True
            except Exception:
                pass
            return False

        # Periodically re-initialize to refresh shared memory mappings
        now = time.time()
        if now - self._last_reinit > self._reinit_interval:
            self._last_reinit = now
            try:
                self.ir.shutdown()
                if not self.ir.startup():
                    # Startup failed — next tick will retake the disconnect path.
                    self.connected = False
                    self._reset_yaml_cache()
                    return False
                self._cache_session_data()
            except Exception:
                # Any exception during re-init -> force full reconnect next tick.
                self.connected = False
                self._reset_yaml_cache()
                return False

        return True

    def _reset_yaml_cache(self):
        """Clear cached YAML so the next access re-fetches."""
        self._driver_info = None
        self._weekend_info = None
        self._session_info = None

    def _cache_session_data(self):
        """Cache session YAML data.

        Each YAML field is fetched independently so a single bad/missing
        section (e.g. SessionInfo not yet published) does not prevent the
        others from being cached.
        """
        try:
            self._driver_info = self.ir['DriverInfo']
        except Exception:
            pass
        try:
            self._weekend_info = self.ir['WeekendInfo']
        except Exception:
            pass
        try:
            self._session_info = self.ir['SessionInfo']
        except Exception:
            pass

    def refresh_session_data(self):
        """Force refresh session YAML."""
        self._cache_session_data()

    @property
    def driver_info(self):
        if self._driver_info is None:
            self._cache_session_data()
        return self._driver_info

    @property
    def weekend_info(self):
        if self._weekend_info is None:
            self._cache_session_data()
        return self._weekend_info

    @property
    def session_info(self):
        if self._session_info is None:
            self._cache_session_data()
        return self._session_info

    def get(self, key):
        """Read a variable using pyirsdk's default (unfrozen) method."""
        try:
            return self.ir[key]
        except Exception:
            return None

    def get_direct(self, key):
        """Read a variable by directly accessing the shared memory mmap.

        Bypasses pyirsdk's buffer selection and freeze mechanism entirely.
        Finds the buffer with the highest tick_count and reads raw bytes.
        This ensures we always get the absolute latest data.
        """
        try:
            ir = self.ir
            if not ir._shared_mem or key not in ir._var_headers_dict:
                return None

            vh = ir._var_headers_dict[key]

            # Find the buffer with the highest tick count by reading
            # tick_count directly from the mmap (not from cached structures)
            best_buf_idx = 0
            best_tick = -1
            for vb in ir._header.var_buf:
                tc = vb.tick_count
                if tc > best_tick:
                    best_tick = tc
                    best_buf_idx = vb.buf_offset

            # Read directly from the live mmap at the correct offset
            fmt = 'f' if vh.type == 4 else ('d' if vh.type == 5 else 'i')
            full_fmt = fmt * vh.count
            offset = best_buf_idx + vh.offset

            data = struct.unpack_from(full_fmt, ir._shared_mem, offset)

            if vh.count == 1:
                return data[0]
            return list(data)

        except Exception:
            return None

    def freeze(self):
        """Freeze the telemetry buffer for atomic reads of non-tire data."""
        try:
            self.ir.freeze_var_buffer_latest()
        except Exception:
            pass

    def unfreeze(self):
        """Unfreeze the buffer."""
        try:
            self.ir.unfreeze_var_buffer_latest()
        except Exception:
            pass

    def is_on_track(self):
        """Check if the player car is currently on track."""
        val = self.get('IsOnTrack')
        if val is None:
            return False
        return bool(val)

    def shutdown(self):
        """Clean shutdown."""
        self.ir.shutdown()
        self.connected = False
