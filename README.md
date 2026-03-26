# iRacing Telemetry Analysis

Real-time telemetry acquisition, live track visualization, and post-session performance analysis for [iRacing](https://www.iracing.com/).

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows)

---

## Features

### Live Telemetry (10 Hz)
- **Real-time timing** - lap times, sector splits, positions, gaps to cars ahead/behind
- **Catch calculator** - EMA-based pace estimation blended with live gap trend to predict when you'll catch (or be caught by) nearby cars
- **Weather monitoring** - air temp, track temp, wind speed/direction, humidity

### Track Map GUI
- **Automatic track mapping** - records your first lap to build a world-coordinate track layout using Speed + YawNorth integration
- **Track database** - saves mapped layouts as JSON; auto-loads on subsequent sessions (no re-mapping needed)
- **Live car positions** - all cars rendered on the map in real-time, color-coded by position (gold/silver/bronze for top 3)
- **Coverage-based completion** - mapping requires 92% track coverage, not just a lap counter

### Data Logging
- **Detailed CSV** (`telemetry_detailed.csv`) - 48 channels at 10 Hz: speed, throttle, brake, gear, RPM, steering, G-forces, fuel, gaps, incidents, and more
- **Lap summary CSV** (`lap_summary.csv`) - per-lap aggregated stats: lap time, position change, fuel used, avg/max speed, gear shifts
- **Continuous lap numbering** - handles iRacing's lap counter resets in Practice mode

### Post-Session Analysis
- **Session summary** (`session_summary.txt`) - start/finish position, best/worst/avg lap (excl. out-lap), overtake history, incidents, fuel consumption, speed stats
- **Position graph** (`position_graph.png`) - lap-by-lap position chart with dark theme, best position highlighted
- **Lap overlay analysis** (`lap_analysis.png`) - all laps overlaid per channel (speed, throttle, brake, gear, steering, delta-to-best) with consistency bands showing where you're inconsistent
- **Delta-to-best plot** (`lap_delta_analysis.png`) - speed/throttle/brake delta to your fastest lap, showing exactly where time is gained or lost

---

## Installation

### Prerequisites
- **Windows** (iRacing is Windows-only)
- **Python 3.10+**
- **iRacing** installed and running

### Setup

```bash
git clone https://github.com/WuffMiauPups/iRacing-Telemetry-Analysis.git
cd iRacing-Telemetry-Analysis
pip install -r requirements.txt
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [pyirsdk](https://github.com/kutu/pyirsdk) | >= 1.3.0 | iRacing shared memory SDK |
| [rich](https://github.com/Textualize/rich) | >= 13.0.0 | Terminal UI (panels, tables, live display) |
| [matplotlib](https://matplotlib.org/) | >= 3.7.0 | Post-session plots and analysis charts |
| [numpy](https://numpy.org/) | >= 1.24.0 | Array operations and interpolation |

> **Note:** `tkinter` is used for the track map GUI and ships with standard Python on Windows.

---

## Usage

1. Start **iRacing** and join a session (Practice, Qualify, or Race)
2. Run the telemetry tool:

```bash
python main.py
```

3. The tool will:
   - Connect to iRacing via shared memory
   - Show live timing data in the terminal (1 Hz refresh)
   - Open a track map window (if mapping data exists or once a mapping lap is complete)
   - Log all telemetry to CSV at 10 Hz

4. Press **Ctrl+C** to stop - the tool generates session summary, position graph, and lap analysis plots automatically

### Output

All session data is saved to `race_logs/<date>_<track>_<session>/`:

```
race_logs/
  2026-03-25_11-47_Oulton_Park_Circuit_Practice/
    telemetry_detailed.csv      # 48-channel raw telemetry (10 Hz)
    lap_summary.csv             # Per-lap aggregated stats
    session_summary.txt         # Human-readable session report
    position_graph.png          # Position vs. lap chart
    lap_analysis.png            # Multi-channel lap overlay
    lap_delta_analysis.png      # Delta-to-best-lap analysis
```

---

## Project Structure

```
iRacing-Telemetry-Analysis/
  main.py                     # Entry point - telemetry loop, display, logging

  telemetry/
    connection.py             # iRacing SDK wrapper (shared memory access)
    timing.py                 # Sector tracking, EMA catch calculator
    session.py                # Weather and session info
    data_logger.py            # CSV logging (tick + lap summary)
    track_map.py              # Track layout mapping and position interpolation
    track_db.py               # JSON track database (save/load layouts)
    lap_analysis.py           # Matplotlib lap overlay and delta plots
    session_summary.py        # Text summary and position graph

  display/
    renderer.py               # Rich terminal UI renderer
    map_window.py              # Tkinter track map GUI

  track_db/                   # Saved track layouts (auto-generated JSON)
  race_logs/                  # Session output folders (auto-generated)
  requirements.txt            # Python dependencies
```

---

## How It Works

### Catch Calculator

Uses an **Exponential Moving Average (EMA)** blended with live gap trend:

```
Pace_n = alpha * LapTime_n + (1 - alpha) * Pace_(n-1)    where alpha = 0.4
```

- **Outlier filter**: Laps > 7% slower than current EMA are discarded before updating (off-tracks, incidents)
- **Blended prediction**: 60% EMA pace delta + 40% live gap trend (linear regression over last 30s)
- Predicts catch time in both laps and seconds

### Track Mapping

Integrates car velocity in world coordinates to build the track shape:

```
x += speed * sin(yaw_north) * dt
y += speed * cos(yaw_north) * dt
```

- Completion based on **coverage** (92% of 100 track-position buckets), not lap count
- Layouts saved to `track_db/` as JSON for instant loading on future sessions
- Binary search interpolation maps any car's `LapDistPct` to (x, y) screen coordinates

### Telemetry Channels (48 per tick)

Speed, throttle, brake, clutch, gear, RPM, steering angle, position, class position, current/last/best lap times, gaps ahead/behind, incidents, fuel level/usage, lateral/longitudinal acceleration, yaw/pitch/roll, velocity XYZ, oil temp/pressure, water temp, voltage, session flags, pit status, delta-to-best-lap.

---

## Known Limitations

- **Tire data unavailable** - iRacing blocks external software from reading live tire temperatures and wear. Data is only available when stationary in the pit box.
- **Windows only** - iRacing and its shared memory interface are Windows-exclusive.
- **First lap required** - A mapping lap is needed before the track map displays car positions (unless a saved layout exists in the database).

---

## Future Ideas

- Voice-powered race engineer (TTS/STT integration with local LLM)
- Multi-driver comparison from shared telemetry files
- Fuel strategy calculator with pit window predictions
- Real-time sector-by-sector delta display

---

*Built with [pyirsdk](https://github.com/kutu/pyirsdk), [Rich](https://github.com/Textualize/rich), and [Matplotlib](https://matplotlib.org/).*
