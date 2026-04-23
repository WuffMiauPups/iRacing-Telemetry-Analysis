"""Mini-sector time attribution and theoretical best lap.

21 equal-width mini-sectors on LapDistPct [0, 1], arranged as
3 main sectors of 7 mini-sectors each (main boundaries at indices 7 and 14).
"""

import os
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


N_MINI_SECTORS = 21
N_MAIN_SECTORS = 3
MINI_PER_MAIN = N_MINI_SECTORS // N_MAIN_SECTORS  # = 7
BOUNDARIES = [i / N_MINI_SECTORS for i in range(N_MINI_SECTORS + 1)]


def _interp_time_at_pct(lap_ticks, target_pct, lap_time=None):
    """Linear-interpolate LapCurrentLapTime at a given LapDistPct.

    lap_ticks is a list of (pct, values_dict) sorted by pct.

    Endpoint handling uses iRacing's semantics:
      - pct=0 is the S/F line; LapCurrentLapTime is 0 by definition.
      - pct=1 is the NEXT S/F crossing; time is the full LapTime, if known.
    Between 0 and the first recorded tick we interpolate from the synthetic
    (0, 0) anchor. Between the last tick and 1.0 we interpolate to
    (1.0, lap_time) if lap_time is provided, otherwise return None.
    """
    if not lap_ticks:
        return None

    if target_pct <= 0:
        return 0.0
    if target_pct >= 1:
        return lap_time  # may be None — caller treats sector as missing

    first_pct, first_v = lap_ticks[0]
    last_pct, last_v = lap_ticks[-1]
    first_t = first_v.get('LapCurrentLapTime')
    last_t = last_v.get('LapCurrentLapTime')

    # Gap before first recorded tick: interpolate from (0, 0) → (first_pct, first_t).
    if target_pct < first_pct:
        if first_pct <= 0 or first_t is None:
            return None
        frac = target_pct / first_pct
        return frac * first_t

    # Gap after last recorded tick: interpolate from (last_pct, last_t) → (1, lap_time).
    if target_pct > last_pct:
        if lap_time is None or last_t is None or last_pct >= 1:
            return None
        span = 1.0 - last_pct
        if span <= 0:
            return last_t
        frac = (target_pct - last_pct) / span
        return last_t + frac * (lap_time - last_t)

    for i in range(1, len(lap_ticks)):
        p1, v1 = lap_ticks[i]
        if p1 < target_pct:
            continue
        p0, v0 = lap_ticks[i - 1]
        t0 = v0.get('LapCurrentLapTime')
        t1 = v1.get('LapCurrentLapTime')
        if t0 is None or t1 is None:
            return None
        if p1 == p0:
            return t1
        frac = (target_pct - p0) / (p1 - p0)
        return t0 + frac * (t1 - t0)
    return None


def _trim_lap_ticks(lap_ticks, lap_time=None):
    """Keep only ticks belonging to this lap's actual S/F-to-S/F interval.

    Two iRacing quirks the trimmer handles:
      - `CarIdxLap` increments at the S/F line but `LapCurrentLapTime` can
        carry the previous lap's value for a tick or two. Those leading
        ticks are dropped via a reset-detection scan (large drop in LCLT).
      - Similarly at the end of a lap, a few ticks can be tagged with the
        old lap number while LCLT has already overshot the true LapTime
        (the counter hasn't caught up). When `lap_time` is known, drop any
        trailing tick where LCLT > lap_time + small epsilon.

    Returns None if the trimmed body is still non-monotonic (pit/tow
    mid-lap), otherwise the trimmed tick list.
    """
    last_reset_idx = 0
    last_t = None
    for i, (_, v) in enumerate(lap_ticks):
        t = v.get('LapCurrentLapTime')
        if t is None:
            continue
        if last_t is not None and t < last_t - 0.5:
            last_reset_idx = i
        last_t = t

    trimmed = lap_ticks[last_reset_idx:]

    # Validate monotonicity of the trimmed portion
    last_t = None
    for _, v in trimmed:
        t = v.get('LapCurrentLapTime')
        if t is None:
            continue
        if last_t is not None and t < last_t - 0.5:
            return None
        last_t = t

    # Drop trailing "overshoot" ticks where LCLT > lap_time (post-S/F bleed-in).
    if lap_time is not None:
        eps = 0.05
        while trimmed:
            t = trimmed[-1][1].get('LapCurrentLapTime')
            if t is not None and t > lap_time + eps:
                trimmed = trimmed[:-1]
            else:
                break

    return trimmed


# Back-compat alias — older callers (worktree, tests) import the old name.
_trim_to_monotonic = _trim_lap_ticks


MIN_COVERAGE_FOR_SECTORS = 0.90  # lap must cover >=90% of track for sectors
MAX_FIRST_PCT = 0.05             # recorded data must start within first 5%
MIN_LAST_PCT = 0.99              # recorded data must reach last 1% of track
MAX_LAP_TIME_INCONSISTENCY = 0.5  # s; reject lap if LCLT/lap_time disagree


def compute_lap_sectors(lap_ticks, lap_time=None):
    """Return list of 21 sector times for one lap. None for unreachable sectors.

    Pass `lap_time` (the full LapTime from lap_summary.csv) so sector 0 and
    sector 20 can anchor correctly at the S/F line. Without it, those edge
    sectors return None because the recorded telemetry never covers pct=0
    or pct=1 exactly (iRacing logs first tick a few hundred ms past S/F).

    Laps are rejected (all-None) when:
      - Coverage < MIN_COVERAGE_FOR_SECTORS (partial lap).
      - lap_time disagrees with tick-level LapCurrentLapTime by more than
        MAX_LAP_TIME_INCONSISTENCY seconds (iRacing occasionally desyncs
        `LastLapTime` from `LapCurrentLapTime` around S/F — those laps'
        sector math is untrustworthy).
    """
    trimmed = _trim_lap_ticks(lap_ticks, lap_time=lap_time)
    if trimmed is None or len(trimmed) < 10:
        return [None] * N_MINI_SECTORS

    first_pct = trimmed[0][0]
    last_pct = trimmed[-1][0]
    coverage = last_pct - first_pct
    if coverage < MIN_COVERAGE_FOR_SECTORS:
        return [None] * N_MINI_SECTORS

    # Endpoint coverage: for the lap_time anchor to make sense, recorded
    # data must reach within 1% of the S/F line at both ends. Otherwise the
    # (last_pct, last_t) → (1.0, lap_time) interpolation spans too much
    # un-sampled track and can invert the time axis.
    if first_pct > MAX_FIRST_PCT or last_pct < MIN_LAST_PCT:
        return [None] * N_MINI_SECTORS

    # Data-integrity check: the last tick's LCLT should be slightly *below*
    # lap_time (iRacing samples every ~0.1s, so the final tick before S/F
    # has LCLT ≈ lap_time - 0.1). More than MAX_LAP_TIME_INCONSISTENCY means
    # iRacing's clocks disagree; the lap can't be trusted for sectoring.
    if lap_time is not None:
        last_t = trimmed[-1][1].get('LapCurrentLapTime')
        if last_t is not None and abs(last_t - lap_time) > MAX_LAP_TIME_INCONSISTENCY:
            return [None] * N_MINI_SECTORS

    t_at = [_interp_time_at_pct(trimmed, b, lap_time=lap_time) for b in BOUNDARIES]
    sectors = []
    for i in range(N_MINI_SECTORS):
        a, b = t_at[i], t_at[i + 1]
        if a is None or b is None or b <= a:
            sectors.append(None)
        else:
            sectors.append(b - a)
    return sectors


def compute_theoretical_best(laps_sectors):
    """Sum of fastest sector time across all laps, per mini-sector.

    laps_sectors: dict lap_num -> list[21] of (float|None)
    Returns (total_seconds_or_None, donor_laps: list[int|None] length 21).
    """
    donors = [None] * N_MINI_SECTORS
    best_times = [None] * N_MINI_SECTORS
    for i in range(N_MINI_SECTORS):
        for lap_num, sectors in laps_sectors.items():
            t = sectors[i] if i < len(sectors) else None
            if t is None:
                continue
            if best_times[i] is None or t < best_times[i]:
                best_times[i] = t
                donors[i] = lap_num
    if any(t is None for t in best_times):
        return None, donors
    return sum(best_times), donors


def _format_time(seconds):
    if seconds is None:
        return '—'
    m = int(seconds // 60)
    s = seconds - m * 60
    return f'{m}:{s:05.2f}'


def build_sector_figure(laps_sectors, lap_times, best_lap,
                         theoretical_best_total, donor_laps, figure=None):
    """Render the mini-sector heatmap onto a matplotlib Figure.

    If `figure` is provided, draws into it (for Qt embedding); otherwise
    creates a new Figure sized to the lap count (for PNG export). Uses
    the OO API so it works with any backend.

    Returns the Figure, or None if laps_sectors is empty.
    """
    from matplotlib.figure import Figure
    import matplotlib as _mpl

    if not laps_sectors:
        return None

    lap_nums = sorted(laps_sectors.keys())
    n_laps = len(lap_nums)

    # Sector bests for coloring
    sector_best = [None] * N_MINI_SECTORS
    for i in range(N_MINI_SECTORS):
        vals = [laps_sectors[ln][i] for ln in lap_nums if laps_sectors[ln][i] is not None]
        if vals:
            sector_best[i] = min(vals)

    # Delta matrix (rows=laps, cols=sectors). NaN where sector missing.
    matrix = np.full((n_laps, N_MINI_SECTORS), np.nan)
    for r, ln in enumerate(lap_nums):
        for c in range(N_MINI_SECTORS):
            t = laps_sectors[ln][c]
            if t is not None and sector_best[c] is not None:
                matrix[r, c] = t - sector_best[c]

    if figure is None:
        figure = Figure(figsize=(18, max(4, 0.3 * n_laps + 2)), facecolor='#1a1a2e')
    else:
        figure.clear()
        figure.patch.set_facecolor('#1a1a2e')

    ax = figure.add_subplot(111)
    ax.set_facecolor('#16213e')

    cmap = _mpl.cm.RdYlGn_r.copy()
    cmap.set_bad(color='#333333')
    vmax = np.nanpercentile(matrix, 95) if np.any(~np.isnan(matrix)) else 1.0
    im = ax.imshow(np.ma.masked_invalid(matrix), aspect='auto', cmap=cmap,
                   vmin=0, vmax=max(vmax, 0.1), interpolation='nearest')

    ax.set_xticks(range(N_MINI_SECTORS))
    ax.set_xticklabels([f'{i+1}' for i in range(N_MINI_SECTORS)],
                       color='white', fontsize=8)
    ax.set_yticks(range(n_laps))
    row_labels = []
    for ln in lap_nums:
        lt = lap_times.get(ln)
        base = f'R{ln}'
        if lt:
            base += f' ({_format_time(lt)})'
        if ln == best_lap:
            base += ' BEST'
        row_labels.append(base)
    ax.set_yticklabels(row_labels, color='white', fontsize=8)

    for idx in (MINI_PER_MAIN - 0.5, 2 * MINI_PER_MAIN - 0.5):
        ax.axvline(idx, color='white', linewidth=2.0, alpha=0.8)

    ax.set_xlabel('Mini-Sektor (1–21, S1/S2/S3 getrennt durch weiße Linien)',
                  color='white', fontsize=11)
    ax.set_ylabel('Runde', color='white', fontsize=11)
    for spine in ax.spines.values():
        spine.set_color('#333333')

    title = 'Mini-Sektor Delta zur Sektor-Bestzeit [s]'
    if theoretical_best_total is not None:
        title += f'    |    Theoretische Bestzeit: {_format_time(theoretical_best_total)}'
    ax.set_title(title, color='white', fontsize=13, fontweight='bold')

    cbar = figure.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.yaxis.set_tick_params(color='white')
    cbar.outline.set_edgecolor('#333333')
    for lbl in cbar.ax.get_yticklabels():
        lbl.set_color('white')

    if donor_laps and any(d is not None for d in donor_laps):
        donor_str = 'Sektor-Donors: ' + ' '.join(
            f'S{i+1}=R{d}' if d is not None else f'S{i+1}=—'
            for i, d in enumerate(donor_laps))
        figure.text(0.01, 0.01, donor_str, color='#888888',
                     fontsize=7, family='monospace')

    figure.tight_layout()
    return figure


def render_sector_plot(session_dir, laps_sectors, lap_times, best_lap,
                        theoretical_best_total, donor_laps):
    """Write mini_sectors.png to disk. Thin wrapper over build_sector_figure."""
    fig = build_sector_figure(laps_sectors, lap_times, best_lap,
                               theoretical_best_total, donor_laps)
    if fig is None:
        return None
    out_path = os.path.join(session_dir, 'mini_sectors.png')
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(),
                 edgecolor='none', bbox_inches='tight')
    return out_path
