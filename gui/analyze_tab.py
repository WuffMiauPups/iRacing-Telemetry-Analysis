"""Analyze tab — browse race_logs/ and compare laps interactively."""

import os

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QTreeView, QListWidget,
    QListWidgetItem, QSplitter, QLabel, QPushButton, QTabWidget,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QBrush

import csv

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar

from gui.lap_plot_widget import LapPlotWidget
from gui.log_browser_model import LogBrowserModel
from telemetry.lap_data import (
    load_with_metadata, filter_laps, get_lap_times_from_summary,
)
from telemetry.session_meta import load_session_meta
from telemetry.mini_sectors import (
    compute_lap_sectors, compute_theoretical_best, build_sector_figure,
)
from telemetry.variance_analysis import (
    detect_brake_points, detect_throttle_releases,
    cluster_events_across_laps, build_variance_figure,
)


def _fmt_lap_time(seconds):
    if seconds is None:
        return '—'
    m = int(seconds // 60)
    s = seconds - m * 60
    return f'{m}:{s:05.2f}'


def _resolve_session_type(session_dir):
    """session_meta.json first; fall back to parsing the folder name."""
    meta = load_session_meta(session_dir) or {}
    st = meta.get('session_type')
    if st:
        return st
    folder = os.path.basename(os.path.normpath(session_dir))
    for suffix in ('Race', 'Qualify', 'Lone_Qualify', 'Practice',
                   'Offline_Testing', 'Time_Attack'):
        if folder.endswith('_' + suffix):
            return suffix.replace('_', ' ')
    return None


BEST_LAP_BG = QColor(20, 70, 35)


class AnalyzeTab(QWidget):
    """Log-tree + lap checkbox list + interactive plots."""

    def __init__(self, race_logs_dir, parent=None):
        super().__init__(parent)
        self._race_logs_dir = race_logs_dir
        self._current_session_dir = None
        self._build_ui()
        self.refresh_logs()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # Left: log tree + refresh button
        left_wrap = QWidget()
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(0, 0, 0, 0)

        refresh_btn = QPushButton("Aktualisieren")
        refresh_btn.clicked.connect(self.refresh_logs)
        left_layout.addWidget(refresh_btn)

        self.tree = QTreeView()
        self.tree.setHeaderHidden(False)
        self.tree.setSelectionMode(QTreeView.SingleSelection)
        self.model = LogBrowserModel(self._race_logs_dir)
        self.tree.setModel(self.model)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_selection)
        left_layout.addWidget(self.tree, 1)

        splitter.addWidget(left_wrap)

        # Middle: summary label + lap checkbox list
        mid_wrap = QWidget()
        mid_layout = QVBoxLayout(mid_wrap)
        mid_layout.setContentsMargins(0, 0, 0, 0)

        self.summary_label = QLabel('Session wählen …')
        self.summary_label.setStyleSheet(
            'color: #00FF66; font-family: Consolas; font-size: 11px;'
            ' padding: 4px; background: #16213e;'
        )
        self.summary_label.setWordWrap(True)
        mid_layout.addWidget(self.summary_label)

        self.export_btn = QPushButton("Theoretische Bestzeit exportieren …")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_theoretical)
        mid_layout.addWidget(self.export_btn)

        mid_layout.addWidget(QLabel("Runden (Häkchen = sichtbar)"))

        self.lap_list = QListWidget()
        self.lap_list.itemChanged.connect(self._on_lap_item_changed)
        mid_layout.addWidget(self.lap_list, 1)

        splitter.addWidget(mid_wrap)

        # Right: tabbed plots (overlay | mini-sectors | variance)
        self.right_tabs = QTabWidget()

        self.plot = LapPlotWidget()
        self.plot.legend_lap_picked.connect(self._on_legend_pick)
        self.right_tabs.addTab(self.plot, "Runden-Overlay")

        # Mini-sector heatmap
        self.sector_fig = Figure(facecolor='#1a1a2e')
        self.sector_canvas = FigureCanvas(self.sector_fig)
        sector_wrap = QWidget()
        sector_layout = QVBoxLayout(sector_wrap)
        sector_layout.setContentsMargins(0, 0, 0, 0)
        sector_layout.addWidget(NavigationToolbar(self.sector_canvas, sector_wrap))
        sector_layout.addWidget(self.sector_canvas, 1)
        self.right_tabs.addTab(sector_wrap, "Mini-Sektoren")

        # Brake/throttle variance
        self.variance_fig = Figure(facecolor='#1a1a2e')
        self.variance_canvas = FigureCanvas(self.variance_fig)
        variance_wrap = QWidget()
        variance_layout = QVBoxLayout(variance_wrap)
        variance_layout.setContentsMargins(0, 0, 0, 0)
        variance_layout.addWidget(NavigationToolbar(self.variance_canvas, variance_wrap))
        variance_layout.addWidget(self.variance_canvas, 1)
        self.right_tabs.addTab(variance_wrap, "Brake/Throttle Varianz")

        # Lap-time progression chart
        self.progress_fig = Figure(facecolor='#1a1a2e')
        self.progress_canvas = FigureCanvas(self.progress_fig)
        progress_wrap = QWidget()
        progress_layout = QVBoxLayout(progress_wrap)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.addWidget(NavigationToolbar(self.progress_canvas, progress_wrap))
        progress_layout.addWidget(self.progress_canvas, 1)
        self.right_tabs.addTab(progress_wrap, "Rundenzeiten-Verlauf")

        splitter.addWidget(self.right_tabs)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 6)

    @Slot()
    def refresh_logs(self):
        self.model.rescan()
        self.tree.expandAll()

    @Slot()
    def _on_tree_selection(self, current, _previous):
        session_dir = self.model.session_path(current)
        if not session_dir:
            return
        self._load_session(session_dir)

    def _load_session(self, session_dir):
        self._current_session_dir = session_dir
        # Cached analysis state used by the export button.
        self._laps_sectors = None
        self._tb_total = None
        self._donors = None
        self._best_lap = None
        self._best_lap_total = None
        self.export_btn.setEnabled(False)

        result = self.plot.load_session(session_dir)
        self._populate_lap_list(result)
        self._load_sector_and_variance(session_dir)

    def _load_sector_and_variance(self, session_dir):
        """Compute mini-sectors + variance for the session, refresh both
        figure canvases, and update the theoretical-best summary label."""
        csv_path = os.path.join(session_dir, 'telemetry_detailed.csv')
        if not os.path.exists(csv_path):
            self.summary_label.setText('Keine Telemetrie gefunden.')
            self.sector_fig.clear()
            self.sector_canvas.draw_idle()
            self.variance_fig.clear()
            self.variance_canvas.draw_idle()
            return

        try:
            laps, lap_meta, race_bounds = load_with_metadata(csv_path)
        except Exception as e:
            self.summary_label.setText(f'Fehler beim Laden: {e}')
            return

        session_type = _resolve_session_type(session_dir)
        sized = {k: v for k, v in laps.items() if len(v) > 50 and k >= 0}
        kept, skip_reasons = filter_laps(sized, lap_meta, race_bounds, session_type)
        lap_times = get_lap_times_from_summary(session_dir)

        if not kept:
            self.summary_label.setText('Keine gültigen Runden nach Filterung.')
            self.sector_fig.clear()
            self.sector_canvas.draw_idle()
            self.variance_fig.clear()
            self.variance_canvas.draw_idle()
            return

        # Mini-sectors + theoretical best (lap_times keys are now 0-based).
        laps_sectors = {ln: compute_lap_sectors(kept[ln],
                                                 lap_time=lap_times.get(ln))
                        for ln in kept}
        tb_total, donors = compute_theoretical_best(laps_sectors)

        best_lap = None
        best_lap_total = None
        for ln, secs in laps_sectors.items():
            if any(s is None for s in secs):
                continue
            total = sum(secs)
            if best_lap_total is None or total < best_lap_total:
                best_lap_total = total
                best_lap = ln

        # Summary text
        lines = []
        if best_lap_total is not None:
            lines.append(f'Beste Runde: R{best_lap}  {_fmt_lap_time(best_lap_total)}')
        if tb_total is not None:
            gain = (best_lap_total - tb_total) if best_lap_total is not None else None
            gain_txt = f'  (–{gain:.2f}s Potenzial)' if gain is not None else ''
            lines.append(f'Theoretische Bestzeit: {_fmt_lap_time(tb_total)}{gain_txt}')
        else:
            complete_n = sum(1 for s in laps_sectors.values() if all(x is not None for x in s))
            lines.append(f'Theoretische Bestzeit: — (nur {complete_n} vollständige Runden)')
        if session_type:
            lines.append(f'Session: {session_type}  |  {len(kept)} gefilterte Runden')
        self.summary_label.setText('\n'.join(lines))

        # Stash state for the CSV export button.
        self._laps_sectors = laps_sectors
        self._tb_total = tb_total
        self._donors = donors
        self._best_lap = best_lap
        self._best_lap_total = best_lap_total
        self.export_btn.setEnabled(tb_total is not None)

        # Mini-sector heatmap
        build_sector_figure(laps_sectors, lap_times, best_lap,
                             tb_total, donors, figure=self.sector_fig)
        self.sector_canvas.draw_idle()

        # Brake/throttle variance
        brake_events = {ln: detect_brake_points(kept[ln]) for ln in kept}
        throttle_events = {ln: detect_throttle_releases(kept[ln]) for ln in kept}
        brake_clusters = cluster_events_across_laps(brake_events)
        throttle_clusters = cluster_events_across_laps(throttle_events)
        built = build_variance_figure(brake_clusters, throttle_clusters,
                                       figure=self.variance_fig)
        if built is None:
            self.variance_fig.clear()
            ax = self.variance_fig.add_subplot(111)
            ax.set_facecolor('#16213e')
            ax.text(0.5, 0.5, 'Keine konsistenten Brems-/Throttle-Events erkannt',
                    color='#888888', ha='center', va='center',
                    transform=ax.transAxes, fontsize=12)
            ax.set_xticks([])
            ax.set_yticks([])
        self.variance_canvas.draw_idle()

        # Lap-time progression chart
        self._render_progress(lap_times, kept, skip_reasons, best_lap,
                               tb_total, best_lap_total)

    def _render_progress(self, lap_times, kept_laps, skip_reasons, best_lap,
                           tb_total, best_lap_total):
        """Scatter/line of lap-number → lap-time. Skipped laps marked with
        grey X; valid laps green (best) or white; dashed horizontal line at
        theoretical best."""
        fig = self.progress_fig
        fig.clear()
        fig.patch.set_facecolor('#1a1a2e')
        ax = fig.add_subplot(111)
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white', labelsize=9)
        ax.grid(True, alpha=0.15, color='white')
        for spine in ax.spines.values():
            spine.set_color('#333333')

        # Build (lap_num, time, is_kept) per lap that has a summary time.
        valid_x, valid_y, valid_colors = [], [], []
        skip_x, skip_y = [], []
        for ln in sorted(lap_times.keys()):
            lt = lap_times.get(ln)
            if lt is None:
                continue
            if ln in kept_laps:
                valid_x.append(ln)
                valid_y.append(lt)
                valid_colors.append('#00FF66' if ln == best_lap else '#ffffff')
            elif ln in skip_reasons:
                skip_x.append(ln)
                skip_y.append(lt)

        if valid_x:
            ax.plot(valid_x, valid_y, color='#3a7bd5', linewidth=1.0,
                     alpha=0.6, zorder=2)
            ax.scatter(valid_x, valid_y, c=valid_colors, s=60, zorder=3,
                        edgecolors='#0d1b2a', linewidths=0.8)
            # Annotate best
            if best_lap is not None and best_lap_total is not None:
                ax.annotate(f'R{best_lap}  {_fmt_lap_time(best_lap_total)}',
                             (best_lap, best_lap_total),
                             textcoords='offset points', xytext=(8, 8),
                             color='#00FF66', fontsize=10, fontweight='bold')
        if skip_x:
            ax.scatter(skip_x, skip_y, marker='x', c='#888888', s=50, zorder=2,
                        label='gefiltert (Pit/Start/Finish)')

        if tb_total is not None:
            ax.axhline(tb_total, color='#00FF66', linestyle='--', linewidth=1.5,
                        alpha=0.8, zorder=1,
                        label=f'Theoretische Bestzeit {_fmt_lap_time(tb_total)}')

        ax.set_xlabel('Runde', color='white', fontsize=11)
        ax.set_ylabel('Rundenzeit [s]', color='white', fontsize=11)
        ax.set_title('Rundenzeiten-Verlauf', color='white', fontsize=13,
                     fontweight='bold')

        # Only draw the legend when at least one artist has a label
        # (skip_x markers or the theoretical-best dashed line). Valid-lap
        # markers are intentionally unlabeled.
        if skip_x or tb_total is not None:
            leg = ax.legend(loc='upper right', facecolor='#1a1a2e',
                             edgecolor='#333333', labelcolor='white', fontsize=9)
            if leg:
                leg.get_frame().set_alpha(0.9)

        fig.tight_layout()
        self.progress_canvas.draw_idle()

    def _populate_lap_list(self, result):
        self.lap_list.blockSignals(True)
        self.lap_list.clear()
        if not result:
            self.lap_list.blockSignals(False)
            return

        best_lap = result['best_lap']
        for lap_num in result['lap_nums']:
            lt = result['lap_times'].get(lap_num)
            label = f"R{lap_num}"
            if lt is not None:
                mins = int(lt // 60)
                secs = lt % 60
                label += f"   {mins}:{secs:06.3f}"
            if lap_num == best_lap:
                label += "   ★ BEST"

            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, lap_num)
            if lap_num == best_lap:
                item.setBackground(QBrush(BEST_LAP_BG))
            self.lap_list.addItem(item)
        self.lap_list.blockSignals(False)

    @Slot(QListWidgetItem)
    def _on_lap_item_changed(self, item):
        lap_num = item.data(Qt.UserRole)
        visible = item.checkState() == Qt.Checked
        self.plot.set_lap_visible(lap_num, visible)

    @Slot(int)
    def _on_legend_pick(self, lap_num):
        """Legend click came from the plot — flip the matching checkbox.
        The itemChanged handler will flip the plot visibility."""
        for i in range(self.lap_list.count()):
            it = self.lap_list.item(i)
            if it.data(Qt.UserRole) == lap_num:
                new_state = Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked
                it.setCheckState(new_state)
                break

    @Slot()
    def _on_export_theoretical(self):
        """Export 21 mini-sectors + donors + per-sector delta to CSV."""
        if self._laps_sectors is None or self._tb_total is None:
            return
        default_path = os.path.join(self._current_session_dir or '',
                                     'theoretical_best.csv')
        path, _ = QFileDialog.getSaveFileName(
            self, "Theoretische Bestzeit exportieren",
            default_path, "CSV (*.csv)")
        if not path:
            return

        # Per-sector best time + donor lap + delta vs best actual lap.
        n = len(next(iter(self._laps_sectors.values()))) if self._laps_sectors else 21
        best_sectors = [None] * n
        for i in range(n):
            vals = [self._laps_sectors[ln][i]
                    for ln in self._laps_sectors
                    if self._laps_sectors[ln][i] is not None]
            if vals:
                best_sectors[i] = min(vals)
        best_lap_sectors = (self._laps_sectors.get(self._best_lap)
                            if self._best_lap is not None else None)

        try:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                w = csv.writer(f)
                w.writerow(['Sektor', 'Zeit_s', 'Spender_Runde',
                             'Bestrunde_Sektorzeit_s', 'Delta_zu_Bestrunde_s'])
                for i in range(n):
                    bt = best_sectors[i]
                    donor = self._donors[i] if i < len(self._donors) else None
                    bls = best_lap_sectors[i] if best_lap_sectors else None
                    delta = (bls - bt) if (bls is not None and bt is not None) else None
                    w.writerow([
                        i + 1,
                        f'{bt:.4f}' if bt is not None else '',
                        f'R{donor}' if donor is not None else '',
                        f'{bls:.4f}' if bls is not None else '',
                        f'{delta:+.4f}' if delta is not None else '',
                    ])
                # Total row
                w.writerow([])
                w.writerow(['Summe', f'{self._tb_total:.4f}', '',
                             f'{self._best_lap_total:.4f}'
                             if self._best_lap_total is not None else '',
                             f'{(self._best_lap_total - self._tb_total):+.4f}'
                             if self._best_lap_total is not None else ''])
        except Exception as e:
            self.summary_label.setText(f'Export fehlgeschlagen: {e}')
            return
        print(f'[analyze_tab] Theoretische Bestzeit exportiert: {path}')
