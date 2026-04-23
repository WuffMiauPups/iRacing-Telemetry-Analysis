# iRacing Telemetry Analysis

Real-time telemetry acquisition, live track visualization, and full
post-session analysis (Motec-style) for [iRacing](https://www.iracing.com/),
built on PySide6.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows)

Logs every tick to CSV, maps the track on your first lap, and produces a
mini-sector heatmap, theoretical best lap, brake / throttle / steering
variance per corner, and lap overlays when the session ends. The live tab
shows the track, opponents, current-session sector deltas, car status, and
your all-time personal best at the current track. The analyze tab lets you
browse every session you've ever logged.

![Live tab placeholder — drop screenshot here](docs/screenshots/live-tab.png)

---

## Features

### Live tab (driving)

- **Personal-best banner** — shows your all-time fastest lap at the current track and when you set it, scanned across every past session in `race_logs/`.
- **Live sector-delta banner** — as you cross each of the 3 main sector boundaries, shows the just-completed sector's time with delta to your session best. Star marker for new bests.
- **Qualifying panel** — large best/last/delta display, auto-emphasised in Qualifying sessions.
- **Timing panel** — car ahead + you + car behind, with gaps, per-lap delta, sector times, and "will catch / will be caught in ~N laps" projections.
- **Track map** — live positions of all cars, coloured by classification position.
- **Car-status panel** — RPM, oil temp, water temp, voltage, with green/yellow/red threshold coloring.
- **Auto-finalize** — 5 s after iRacing disconnects, if you completed ≥ 1 lap, the session finalizes automatically (plots + summaries). No Ctrl+C required.

### Analyze tab (post-session)

- **Session tree** — all your past sessions grouped by track, with lap-time labels.
- **Lap overlay** — interactive matplotlib plot of Speed / Gas / Brake / Gear / Steering / Delta-to-Best across track position. Click a lap's legend entry to toggle it.
- **Mini-sector heatmap** — 21 sectors × N laps, coloured by delta to sector-best. 3 main-sector dividers drawn. Title shows theoretical best time.
- **Brake / throttle / steering variance** — three per-corner bar charts: hard-braking zones (heavy brake application), throttle-release points (where you lift), and steering turn-in points (derived from `|SteeringWheelAngle| > 11°` — catches every corner, including flat-out sweepers where you don't brake). Each chart shows mean position + standard deviation + lap count across the session, so you can spot which corners you're inconsistent at.
- **Lap-time progression** — scatter + line of lap number → lap time, with skipped laps (out/in/start/finish) marked with grey Xs, theoretical best as a dashed line.
- **CSV export** — dump the 21 theoretical-best sectors + donor laps + delta to best actual lap as a CSV for external analysis.

![Analyze tab placeholder](docs/screenshots/analyze-tab.png)
![Mini-sector heatmap placeholder](docs/screenshots/mini-sectors.png)

### Data logging (always on)

- **Detailed CSV** (`telemetry_detailed.csv`) — 40 channels at 10 Hz: speed, throttle, brake, gear, RPM, steering angle + torque, position, class position, current / last / best lap times, gaps ahead / behind, incidents, fuel level & usage, lateral / longitudinal acceleration, yaw / pitch / roll, velocity XYZ, oil temp & pressure, water temp, voltage, session flags, pit status, delta-to-best-lap.
- **Lap summary CSV** (`lap_summary.csv`) — per-lap aggregated stats: lap time, position change, fuel used, avg / max speed, gear shifts.
- **session_meta.json** — track key, car name, session type, start time. Written once at session start so post-session tools can reload the track layout without an iRacing connection.
- **Continuous lap numbering** — handles iRacing's lap-counter resets in Practice mode.

---

## How the analysis actually works

### Mini-sectors and theoretical best

Each lap is split into 21 equal-width mini-sectors on `LapDistPct ∈ [0, 1]`,
organised as 3 main sectors × 7 mini-sectors. Sector times are interpolated
between ticks using `LapCurrentLapTime`, anchored at (pct=0, t=0) and
(pct=1, t=LapTime). Laps are rejected from the theoretical best if any of
these integrity checks fail:

- Coverage < 90 % of the track (partial laps).
- First recorded pct > 5 % or last pct < 99 % (S/F-crossing data gap).
- `|last_LCLT − LapTime| > 0.5 s` (iRacing clock desync).

Theoretical best = sum of the fastest time recorded in each of the 21
sectors across all accepted laps.

### Lap filtering

`telemetry/lap_data.py:filter_laps` drops:

- Any lap with `OnPitRoad == True` at any tick (outlap / inlap / pit).
- **Race sessions**: laps entirely before green flag, the first lap after
  green (rolling-start artifacts), and laps that span or start after the
  checkered flag. Green / checkered detected via `SessionFlags`.
- **Practice / Qualifying**: just the first lap (flags unreliable).

### Brake / throttle / steering variance

Three parallel detectors:

- **Brake** — rising-edge above 3 % (low enough to catch trail-brake and
  rotation brushes, not just heavy straight-line braking).
- **Throttle** — falling-edge below 90 % (captures every lift, light and
  heavy).
- **Steering** — rising-edge above 0.2 rad (~11°), direction-agnostic. This
  is the one that maps to the track's total corner count (13-16 on
  Hockenheim GP), including flat-out sweepers where you never touch the
  brake.

Events from all laps are clustered by track-position proximity
(ε = 1.5 % ≈ 68 m) — each cluster ≈ one corner. The chart reports per-corner
min / max / mean / standard deviation across the laps in the session.

### Catch calculator (live)

Uses an **Exponential Moving Average** blended with live gap trend:

```
Pace_n = alpha * LapTime_n + (1 - alpha) * Pace_(n-1)    where alpha = 0.4
```

- Outlier filter: laps > 7 % slower than current EMA are discarded before
  updating (off-tracks, incidents).
- Blended prediction: 60 % EMA pace delta + 40 % live gap trend (linear
  regression over the last 30 s).
- Predicts catch time in both laps and seconds, displayed next to the
  ahead / behind entries in the Timing panel.

### Track mapping

Integrates car velocity in world coordinates to build the track shape:

```
x += speed * sin(yaw_north) * dt
y += speed * cos(yaw_north) * dt
```

- Completion based on **coverage** (92 % of 100 track-position buckets),
  not lap count.
- Layouts saved to `track_db/` as JSON for instant loading on future
  sessions.
- Binary search + linear interpolation maps any car's `LapDistPct` to
  (x, y) screen coordinates.

---

## Installation

### Prerequisites

- **Windows** (iRacing is Windows-only)
- **Python 3.10+**
- **iRacing** installed

### Setup

```bash
git clone https://github.com/WuffMiauPups/iRacing-Telemetry-Analysis.git
cd iRacing-Telemetry-Analysis
pip install -r requirements.txt
```

### Dependencies

| Package | Version | Purpose |
|---|---|---|
| [pyirsdk](https://github.com/kutu/pyirsdk) | ≥ 1.3.0 | iRacing shared-memory SDK |
| [PySide6](https://doc.qt.io/qtforpython-6/) | ≥ 6.6 | GUI framework |
| [matplotlib](https://matplotlib.org/) | ≥ 3.7.0 | All plots, embedded in Qt and as PNGs |
| [numpy](https://numpy.org/) | ≥ 1.24.0 | Array operations, interpolation |
| [rich](https://github.com/Textualize/rich) | ≥ 13.0.0 | Terminal UI for the legacy CLI |

---

## Usage

### GUI (recommended)

```bash
python app.py
```

Start iRacing first. The Live tab populates within a second of going
on-track. Close iRacing when you're done with a session — the tool
auto-finalizes after 5 seconds. Your session's folder will contain:

- `telemetry_detailed.csv` — 10 Hz tick data (every channel)
- `lap_summary.csv` — one row per completed lap
- `session_meta.json` — track, car, session type, start time
- `session_summary.txt` — human-readable summary
- `position_graph.png`, `lap_analysis.png`, `lap_delta_analysis.png`,
  `mini_sectors.png`, `brake_throttle_variance.png` — analysis plots

### CLI (legacy)

```bash
python main.py
```

Terminal-only, no interactive analyze UI. Still auto-finalizes on
disconnect. Ctrl+C also works.

### Re-analyze an older session

```bash
python -m telemetry.lap_analysis race_logs/<session_folder>
```

Regenerates all PNGs + prints the theoretical best to stdout. Accepts
`--include-all` to disable the outlap / inlap / start / finish filters,
and `--no-viewer` if you only want the PNGs.

---

## Project layout

```
app.py                     PySide6 GUI entry point
main.py                    Terminal CLI entry point (legacy)
config.py                  Tunables: tick rates, thresholds, UI sizes

gui/
  live_tab.py              Live tab: map + timing + sectors + car status
  analyze_tab.py           Analyze tab: session tree + 4 plot sub-tabs + CSV export
  worker.py                QThread worker that drives iRacing + emits snapshots
  timing_panel.py          Ahead / you / behind gap display
  qualifying_panel.py      Large best-lap display (emphasised in quali)
  map_widget.py            Track map canvas
  car_status_panel.py      RPM / oil / water / voltage with thresholds
  lap_plot_widget.py       Interactive overlay plot (used inside analyze tab)
  log_browser_model.py     Tree model for race_logs/ browsing

telemetry/
  connection.py            Shared-memory interface, non-blocking check_connection
  data_logger.py           10 Hz CSV writers + per-lap aggregation
  lap_analysis.py          Post-session PNG generation + CLI re-analysis
  lap_data.py              Pure data helpers shared by GUI + PNG paths
  mini_sectors.py          21-sector interpolation + theoretical-best math
  variance_analysis.py     Brake / throttle / steering event clustering
  session_meta.py          session_meta.json read/write
  session_history.py       Cross-session PB scanner
  timing.py                SectorTracker + CatchCalculator + TimingMonitor
  session.py               SessionMonitor (track info, weather)
  track_map.py             Live track-layout recorder
  track_db.py              Saved track layouts (JSON) + sector splits
  pit_window.py            Fuel / pit window estimator
  tires.py                 Tire data wrapper (where iRacing exposes it)

display/                   Legacy CLI-only display (Rich terminal + Tk map window)

tests/
  test_lap_data.py         Pure-function tests for the data layer
  test_log_browser_model.py
  test_pure_functions.py

track_db/                  Per-track layout JSON (checked in; small and reusable)
race_logs/                 (gitignored) Per-session output folders
```

---

## Data flow

```
iRacing (shared memory)
  └─► IRacingConnection (non-blocking polling)
        └─► TelemetryWorker (QThread, 10 Hz)
              ├─► DataLogger  ──► race_logs/<session>/telemetry_detailed.csv
              │                   race_logs/<session>/lap_summary.csv
              │                   race_logs/<session>/session_meta.json
              ├─► TrackMapper ──► track_db/<track>.json
              ├─► TimingMonitor (sectors, gaps, catch-time)
              └─► snapshot signal ──► LiveTab.on_snapshot
                                       ├─► TimingPanel
                                       ├─► QualifyingPanel
                                       ├─► MapWidget
                                       ├─► CarStatusPanel
                                       └─► sector-delta + PB labels

on session end (iRacing closes / worker.stop):
  └─► generate_session_summary  ──► session_summary.txt, position_graph.png
  └─► generate_lap_analysis     ──► lap_analysis.png, lap_delta_analysis.png,
                                     mini_sectors.png, brake_throttle_variance.png
```

---

## Screenshots

> Drop screenshots into `docs/screenshots/` with the filenames below and
> they'll render here.

- `docs/screenshots/live-tab.png` — Live tab with PB banner + sector delta
- `docs/screenshots/analyze-tab.png` — Analyze tab with 4 sub-tabs
- `docs/screenshots/mini-sectors.png` — Mini-sector heatmap
- `docs/screenshots/variance.png` — Brake / throttle / steering variance
- `docs/screenshots/lap-overlay.png` — Lap overlay with best lap highlighted

---

## Language

GUI labels are in German ("Warte auf iRacing", "Theoretische Bestzeit",
"Runden", etc.) because the author drives in German. The code and docs are
in English. Open a PR if you want localized labels.

---

## Limitations / known issues

- **Tire data unavailable** — iRacing blocks external software from reading
  live tire temperatures and wear. Data is only available when stationary
  in the pit box.
- **Formula-car focus** — ABS / traction control indicators are
  intentionally not logged (the target car is a Formula without driver
  aids).
- **First lap required** — a mapping lap is needed before the track map
  displays car positions, unless a saved layout exists in `track_db/`.
- **iRacing clock quirks** — iRacing occasionally tags a few ticks past
  S/F with the old lap number. `mini_sectors.py` detects this via a
  `LapTime` consistency check and rejects those laps from theoretical-best
  math.
- **Hard-kill shutdown** — closing the terminal window with the X button
  on Windows does NOT reliably trigger Python's `finally` blocks. The
  QThread's graceful shutdown handles the "iRacing closed" case; the
  "close terminal mid-session" case may skip finalization.

---

## Future ideas

- Voice-powered race engineer (TTS / STT with a local LLM).
- Multi-driver comparison by loading two session folders side-by-side.
- Fuel-strategy calculator with pit-window predictions.
- Live car-status panel enhancements (G-force dot, throttle/brake bar).

---

*Built with [pyirsdk](https://github.com/kutu/pyirsdk),
[PySide6](https://doc.qt.io/qtforpython-6/),
[matplotlib](https://matplotlib.org/), and
[Rich](https://github.com/Textualize/rich).*
