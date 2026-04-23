"""LapPlotWidget — matplotlib-in-Qt lap overlay plot with interactive toggling.

Channels (Speed, Throttle, Brake, Gear, Steering, Delta-to-Best) share the
x-axis (track position 0-100%). Each lap gets one Line2D per channel; we
store them keyed by lap number so the checkbox list in AnalyzeTab can
toggle visibility without redrawing everything.
"""

import numpy as np
import matplotlib
matplotlib.use('QtAgg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Signal

from telemetry.lap_data import (
    CHANNELS,
    load_and_group_laps,
    resample_lap,
    find_best_lap,
    get_lap_times_from_summary,
)


BEST_COLOR = '#00FF66'
BG_FIG = '#1a1a2e'
BG_AX = '#16213e'


class LapPlotWidget(QWidget):
    # Emitted when a user clicks on a lap's legend entry — argument is lap num.
    legend_lap_picked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(facecolor=BG_FIG)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

        # lap_num -> list[Line2D] across all subplots
        self._lines_by_lap = {}
        # legend_artist -> lap_num (for pick events)
        self._legend_to_lap = {}
        # axes keyed by channel for re-use
        self._axes = {}
        self._delta_axes = {}

        self.canvas.mpl_connect('pick_event', self._on_pick)

    # ------------------------------------------------------------------
    def load_session(self, session_dir):
        """Load a session directory and render all laps.

        Returns a dict describing what was loaded:
            {'lap_nums': [...sorted...], 'lap_times': {...}, 'best_lap': int|None}
        or None if there was nothing plottable.
        """
        laps_raw = load_and_group_laps(_csv_path(session_dir))
        if not laps_raw:
            return None

        valid = {k: v for k, v in laps_raw.items() if len(v) > 50 and k >= 0}
        if not valid:
            return None

        lap_nums = sorted(valid.keys())
        lap_times = get_lap_times_from_summary(session_dir)
        best_lap = find_best_lap(lap_times)
        if best_lap is None and lap_nums:
            best_lap = lap_nums[0]

        colors = self._assign_colors(lap_nums, best_lap)
        self._render(valid, lap_nums, lap_times, best_lap, colors)

        return {
            'lap_nums': lap_nums,
            'lap_times': lap_times,
            'best_lap': best_lap,
        }

    def set_lap_visible(self, lap_num, visible):
        """Show or hide all lines belonging to the given lap across subplots."""
        lines = self._lines_by_lap.get(lap_num, [])
        for ln in lines:
            ln.set_visible(visible)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _assign_colors(self, lap_nums, best_lap):
        cmap = plt.cm.coolwarm
        n = len(lap_nums)
        out = {}
        for i, lap in enumerate(lap_nums):
            if lap == best_lap:
                out[lap] = BEST_COLOR
            else:
                out[lap] = cmap(i / max(n - 1, 1))
        return out

    def _render(self, laps_data, lap_nums, lap_times, best_lap, colors):
        self.figure.clear()
        self._lines_by_lap.clear()
        self._legend_to_lap.clear()
        self._axes.clear()
        self._delta_axes.clear()

        # Channel subplots, then one delta subplot at the bottom.
        # Filter out channels that are LapDeltaToBestLap — we'll render our
        # own delta panel computed from speed.
        main_channels = [c for c in CHANNELS if c[0] != 'LapDeltaToBestLap']
        n = len(main_channels) + 1  # + delta panel

        gs = self.figure.add_gridspec(n, 1, hspace=0.12)

        first_ax = None
        for idx, (col, name, unit, inv) in enumerate(main_channels):
            ax = self.figure.add_subplot(gs[idx, 0], sharex=first_ax) if first_ax else self.figure.add_subplot(gs[idx, 0])
            first_ax = first_ax or ax
            self._style_axes(ax)
            self._axes[col] = ax

            for lap in lap_nums:
                pcts, vals = resample_lap(laps_data[lap], col)
                if pcts is None:
                    continue
                is_best = (lap == best_lap)
                line, = ax.plot(
                    pcts, vals,
                    color=colors[lap],
                    linewidth=2.5 if is_best else 0.9,
                    alpha=1.0 if is_best else 0.55,
                    zorder=10 if is_best else 2,
                    label=self._lap_label(lap, lap_times.get(lap), is_best),
                )
                self._lines_by_lap.setdefault(lap, []).append(line)

            if inv:
                ax.invert_yaxis()
            unit_str = f' [{unit}]' if unit else ''
            ax.set_ylabel(f'{name}{unit_str}', color='white', fontsize=9, fontweight='bold')
            if idx < len(main_channels) - 1:
                ax.tick_params(labelbottom=False)

            # Legend on the first subplot only.
            if idx == 0:
                leg = ax.legend(
                    loc='upper right', fontsize=7,
                    ncol=min(len(lap_nums), 5),
                    facecolor=BG_FIG, edgecolor='#333333', labelcolor='white',
                )
                if leg is not None:
                    for leg_line, lap in zip(leg.get_lines(), lap_nums):
                        leg_line.set_picker(5)
                        self._legend_to_lap[leg_line] = lap

        # Delta panel — time delta to best lap, computed from speed vs distance.
        delta_ax = self.figure.add_subplot(gs[n - 1, 0], sharex=first_ax)
        self._style_axes(delta_ax)
        delta_ax.set_ylabel('Delta zu Best [s]', color='white', fontsize=9, fontweight='bold')
        delta_ax.set_xlabel('Streckenposition [%]', color='white', fontsize=10)
        delta_ax.axhline(0, color=BEST_COLOR, linewidth=1.5, linestyle='--', alpha=0.7)
        self._axes['_delta'] = delta_ax

        self._render_delta(delta_ax, laps_data, lap_nums, lap_times, best_lap, colors)

        self.canvas.draw_idle()

    def _render_delta(self, ax, laps_data, lap_nums, lap_times, best_lap, colors):
        """Integrate inverse speed to get time-per-distance and show time delta
        against the best lap."""
        if best_lap is None or best_lap not in laps_data:
            return
        best_pcts, best_speed = resample_lap(laps_data[best_lap], 'Speed_kmh')
        if best_pcts is None:
            return
        # Cumulative time along the best lap at each sample (reference curve).
        best_time = self._cumulative_time(best_pcts, best_speed)

        for lap in lap_nums:
            if lap == best_lap:
                continue
            pcts, speed = resample_lap(laps_data[lap], 'Speed_kmh')
            if pcts is None:
                continue
            speed_aligned = np.interp(best_pcts, pcts, speed)
            lap_time = self._cumulative_time(best_pcts, speed_aligned)
            delta = lap_time - best_time
            line, = ax.plot(
                best_pcts, delta,
                color=colors[lap], linewidth=1.0, alpha=0.8,
                label=self._lap_label(lap, lap_times.get(lap), False),
            )
            self._lines_by_lap.setdefault(lap, []).append(line)

    @staticmethod
    def _cumulative_time(pcts, speed_kmh):
        """Cumulative time in seconds along the track given samples of
        track-position-% and speed in km/h.

        This is an approximate delta visualisation — it ignores the absolute
        distance scale, so the y-axis is 'arbitrary time units' that are
        still useful for comparing laps."""
        speed_ms = np.clip(speed_kmh, 1e-3, None) / 3.6
        # Treat pcts as evenly spaced 0-100 steps; unit distance per step.
        dt = 1.0 / speed_ms
        return np.cumsum(dt)

    @staticmethod
    def _style_axes(ax):
        ax.set_facecolor(BG_AX)
        ax.tick_params(colors='white', labelsize=8)
        ax.grid(True, alpha=0.15, color='white')
        for spine in ax.spines.values():
            spine.set_color('#333333')

    @staticmethod
    def _lap_label(lap_num, lt, is_best):
        label = f'R{lap_num}'
        if lt:
            mins = int(lt // 60)
            secs = lt % 60
            label += f' ({mins}:{secs:05.2f})'
        if is_best:
            label += ' BEST'
        return label

    def _on_pick(self, event):
        lap = self._legend_to_lap.get(event.artist)
        if lap is not None:
            self.legend_lap_picked.emit(lap)


def _csv_path(session_dir):
    import os
    return os.path.join(session_dir, 'telemetry_detailed.csv')
