"""Central configuration constants for the iRacing telemetry tool.

All values here are the long-standing defaults that the rest of the code
relied on inline. Centralising them does not change behavior — every value
matches what the modules used before.
"""

# --- Main loop timing ---
TICK_RATE = 0.1            # 10 Hz telemetry sampling
DISPLAY_RATE = 1.0         # 1 Hz terminal refresh
SESSION_REFRESH = 30       # Refresh session YAML every 30 s

# --- Connection / reconnect ---
REINIT_INTERVAL_S = 10     # Re-init shared memory every 10 s
TICK_STAGNATION_S = 3.0    # Treat as disconnect if SessionTick frozen this long

# --- Sectors ---
DEFAULT_SECTOR_SPLITS = [0.333, 0.666, 1.0]

# --- Catch calculator (EMA pace + live gap blend) ---
EMA_ALPHA = 0.4            # P_n = alpha * L_n + (1 - alpha) * P_(n-1)
LAP_WEIGHT = 0.6           # Weight for EMA-based pace delta
LIVE_WEIGHT = 0.4          # Weight for live gap trend
LAP_TIME_FILTER_MIN_S = 10.0      # Discard laps shorter than this (pit reset)
LAP_TIME_FILTER_MAX_FACTOR = 1.07 # Discard laps slower than this * EMA
LAP_TIME_FILTER_MIN_FACTOR = 0.60 # Discard laps faster than this * EMA
GAP_RATE_MAX = 3.0         # Max plausible s/s gap rate (rejects spikes)
DELTA_CLAMP = 15.0         # Hard clamp on per-lap delta to filter outliers
DELTA_DEADBAND = 0.005     # Treat smaller deltas as zero
GAP_HISTORY_MAX = 30       # Max samples in live-gap rolling window
GAP_HISTORY_WINDOW_S = 30.0  # Time window for gap-rate calculation

# --- Track mapper ---
TRACK_SAMPLE_INTERVAL = 0.003  # Sample every 0.3% of lap distance
TRACK_COVERAGE_THRESHOLD = 0.92  # 92 of 100 buckets needed
TRACK_MIN_POINTS_FOR_FINISH = 100
TRACK_MIN_POINTS_FOR_NORMALIZE = 50
TRACK_LUT_BUCKETS = 1000   # Pre-baked lookup table size for get_position

# --- Map window (Tk) ---
MAP_WINDOW_WIDTH = 700
MAP_WINDOW_HEIGHT = 600
MAP_POLL_MS = 100          # Tk polling interval

# --- Data logger ---
TICK_FLUSH_INTERVAL = 100  # Flush tick CSV every N ticks

# --- Gap modes (env var IRACING_GAP_MODE overrides) ---
GAP_MODE_DEFAULT = 'hybrid'
GAP_MODES_VALID = {'hybrid', 'legacy', 'progress'}

# --- PySide6 GUI (app.py) ---
GUI_WINDOW_W = 1500
GUI_WINDOW_H = 950
GUI_MAP_MIN_W = 550
GUI_MAP_MIN_H = 550
GUI_QUAL_BEST_FONT_PX = 56
GUI_QUAL_LAST_FONT_PX = 32
GUI_QUAL_DELTA_FONT_PX = 32
