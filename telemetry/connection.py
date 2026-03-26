import irsdk
import time
import struct


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
        self._reinit_interval = 10  # Re-init every 10s to refresh mappings

    def connect(self):
        """Connect to iRacing, blocks until iRacing is running."""
        while not self.ir.startup():
            print("Warte auf iRacing...")
            time.sleep(2)
        self.connected = True
        self._last_reinit = time.time()
        self._cache_session_data()

    def check_connection(self):
        """Check if still connected, reconnect if needed."""
        if not self.ir.is_connected:
            self.connected = False
            self._driver_info = None
            self._weekend_info = None
            self._session_info = None
            self.ir.shutdown()
            print("\nVerbindung verloren. Reconnecting...")
            self.connect()
            return

        # Periodically re-initialize to refresh shared memory mappings
        now = time.time()
        if now - self._last_reinit > self._reinit_interval:
            self._last_reinit = now
            try:
                self.ir.shutdown()
                if not self.ir.startup():
                    self.connected = False
                    return
                self._cache_session_data()
            except Exception:
                pass

    def _cache_session_data(self):
        """Cache session YAML data."""
        try:
            self._driver_info = self.ir['DriverInfo']
            self._weekend_info = self.ir['WeekendInfo']
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
