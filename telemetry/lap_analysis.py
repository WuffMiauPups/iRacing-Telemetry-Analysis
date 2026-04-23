"""Lap-by-lap telemetry analysis with overlay plots and consistency bands.

Reads the detailed telemetry CSV, groups by lap, resamples to track position,
and generates multi-channel overlay plots showing where you're fast/slow/inconsistent.
"""

import os
import sys
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.gridspec import GridSpec

from telemetry.lap_data import (
    CHANNELS,
    RESAMPLE_POINTS,
    load_and_group_laps as _load_and_group_laps,
    load_with_metadata,
    filter_laps,
    resample_lap as _resample_lap,
    find_best_lap,
    get_lap_times_from_summary as _get_lap_times_from_summary,
)


def _find_best_lap(_laps_data, lap_times):
    """Back-compat wrapper: older signature took (laps_data, lap_times)."""
    return find_best_lap(lap_times)


def generate_lap_analysis(session_dir, include_all=False):
    """Generate comprehensive lap overlay analysis plots.

    Creates:
    - lap_analysis.png: Multi-channel overlay with consistency bands
    - lap_delta_analysis.png: Delta-to-best-lap plots
    - mini_sectors.png: 21 mini-sector heatmap + theoretical best (best-effort)
    - brake_throttle_variance.png: Per-corner consistency (best-effort)

    Args:
        session_dir: Path to the session folder containing telemetry_detailed.csv
        include_all: If True, skip outlap/inlap/start/finish filtering.
    """
    csv_path = os.path.join(session_dir, 'telemetry_detailed.csv')
    if not os.path.exists(csv_path):
        return None

    # Load everything — laps dict, pit/flag metadata, race-flag bounds.
    laps_data, lap_meta, race_bounds = load_with_metadata(csv_path)
    if not laps_data:
        return None

    # Resolve session_type for filter semantics (from session_meta.json if
    # present, else parse from folder name like "..._Race" / "..._Practice").
    session_type = None
    try:
        from telemetry.session_meta import load_session_meta
        meta_file = load_session_meta(session_dir)
        if meta_file:
            session_type = meta_file.get('session_type')
    except Exception as e:
        print(f"[lap_analysis] loading session_meta: {e}", file=sys.stderr)
    if not session_type:
        folder = os.path.basename(os.path.normpath(session_dir))
        for suffix in ('Race', 'Qualify', 'Lone_Qualify', 'Practice',
                        'Offline_Testing', 'Time_Attack'):
            if folder.endswith('_' + suffix):
                session_type = suffix.replace('_', ' ')
                break

    # Drop laps with too little data first (pit laps, resets, aborted).
    sized = {k: v for k, v in laps_data.items()
             if len(v) > 50 and k >= 0}

    # Then apply outlap/inlap/start/finish filtering.
    valid_laps, skip_reasons = filter_laps(sized, lap_meta, race_bounds,
                                             session_type, include_all=include_all)

    if skip_reasons:
        print(f"[lap_analysis] skipped {len(skip_reasons)} lap(s): "
              + ", ".join(f"R{k}={v}" for k, v in sorted(skip_reasons.items())))

    if len(valid_laps) < 1:
        print("[lap_analysis] no laps remain after filtering; "
              "call with include_all=True to bypass.",
              file=sys.stderr)
        return None

    lap_nums = sorted(valid_laps.keys())

    # Get lap times for labeling
    lap_times = _get_lap_times_from_summary(session_dir)
    best_lap = _find_best_lap(valid_laps, lap_times)

    # If no lap times available, just use the first valid lap as "best"
    if best_lap is None and lap_nums:
        best_lap = lap_nums[0]

    # --- Color scheme ---
    # Best lap = bright green, others colored by lap number
    n_laps = len(lap_nums)
    cmap = plt.cm.coolwarm
    lap_colors = {}
    for i, lap_num in enumerate(lap_nums):
        if lap_num == best_lap:
            lap_colors[lap_num] = '#00FF66'  # Bright green
        else:
            lap_colors[lap_num] = cmap(i / max(n_laps - 1, 1))

    # --- Create figure ---
    # Main channels + consistency subplot for each
    active_channels = []
    for col, name, unit, inv in CHANNELS:
        # Check if any lap has data for this channel
        has_data = False
        for lap_num in lap_nums[:3]:  # Check first 3 laps
            pct, vals = _resample_lap(valid_laps[lap_num], col)
            if pct is not None:
                has_data = True
                break
        if has_data:
            active_channels.append((col, name, unit, inv))

    n_channels = len(active_channels)
    if n_channels == 0:
        return None

    # Each channel gets 2 rows: main plot (3 units) + consistency band (1 unit)
    fig = plt.figure(figsize=(18, 3.5 * n_channels))
    fig.patch.set_facecolor('#1a1a2e')

    gs = GridSpec(n_channels * 2, 1, height_ratios=[3, 1] * n_channels,
                  hspace=0.08)

    for ch_idx, (col, name, unit, inv) in enumerate(active_channels):
        ax_main = fig.add_subplot(gs[ch_idx * 2])
        ax_band = fig.add_subplot(gs[ch_idx * 2 + 1], sharex=ax_main)

        # Style axes
        for ax in [ax_main, ax_band]:
            ax.set_facecolor('#16213e')
            ax.tick_params(colors='white', labelsize=8)
            ax.grid(True, alpha=0.15, color='white')
            for spine in ax.spines.values():
                spine.set_color('#333333')

        # --- Main overlay plot ---
        all_resampled = []
        common_pcts = None

        for lap_num in lap_nums:
            pcts, vals = _resample_lap(valid_laps[lap_num], col)
            if pcts is None:
                continue

            is_best = (lap_num == best_lap)
            color = lap_colors[lap_num]
            lw = 2.5 if is_best else 0.8
            alpha = 1.0 if is_best else 0.5
            zorder = 10 if is_best else 1

            # Label with lap time if available
            lt = lap_times.get(lap_num)
            label = f'R{lap_num}'
            if lt:
                mins = int(lt // 60)
                secs = lt % 60
                label += f' ({mins}:{secs:05.2f})'
            if is_best:
                label += ' BEST'

            ax_main.plot(pcts, vals, color=color, linewidth=lw,
                        alpha=alpha, zorder=zorder, label=label)

            all_resampled.append(vals)
            if common_pcts is None:
                common_pcts = pcts

        if inv:
            ax_main.invert_yaxis()

        # Y label
        unit_str = f' [{unit}]' if unit else ''
        ax_main.set_ylabel(f'{name}{unit_str}', color='white', fontsize=10,
                          fontweight='bold')

        # Legend (compact, outside)
        if ch_idx == 0:
            leg = ax_main.legend(loc='upper right', fontsize=7,
                                ncol=min(n_laps, 5),
                                facecolor='#1a1a2e', edgecolor='#333333',
                                labelcolor='white')

        # Hide x labels on main plot (shared with band below)
        ax_main.tick_params(labelbottom=False)

        # --- Consistency band ---
        if len(all_resampled) >= 2 and common_pcts is not None:
            stacked = np.array(all_resampled)
            mean_vals = np.mean(stacked, axis=0)
            std_vals = np.std(stacked, axis=0)
            min_vals = np.min(stacked, axis=0)
            max_vals = np.max(stacked, axis=0)

            # Plot standard deviation as colored band
            # Color by magnitude: green=consistent, red=inconsistent
            # Normalize std relative to the channel's range
            val_range = np.ptp(mean_vals) if np.ptp(mean_vals) > 0 else 1
            norm_std = std_vals / val_range

            # Create color-mapped fill
            ax_band.fill_between(common_pcts, 0, std_vals,
                                color='#ff4444', alpha=0.4)
            ax_band.plot(common_pcts, std_vals, color='#ff6666',
                        linewidth=0.8, alpha=0.8)

            # Also show min-max range as lighter band
            ax_band.fill_between(common_pcts, std_vals, std_vals * 0,
                                alpha=0.1, color='white')

            ax_band.set_ylabel('StdDev', color='#888888', fontsize=8)
            ax_band.set_ylim(bottom=0)

            # Mark high-inconsistency zones
            threshold = np.percentile(std_vals, 85)
            high_std_mask = std_vals > threshold
            if any(high_std_mask):
                ax_band.fill_between(common_pcts, 0, std_vals,
                                    where=high_std_mask,
                                    color='#ff0000', alpha=0.3)
        else:
            ax_band.set_ylabel('StdDev', color='#888888', fontsize=8)
            ax_band.text(50, 0.5, 'Nicht genug Runden fuer Varianz',
                        color='#555555', ha='center', fontsize=8,
                        transform=ax_band.transAxes)

        # X label only on bottom
        if ch_idx == n_channels - 1:
            ax_band.set_xlabel('Streckenposition [%]', color='white',
                             fontsize=11)
        else:
            ax_band.tick_params(labelbottom=False)

    # --- Title ---
    fig.suptitle('Rundenanalyse - Alle Runden im Vergleich',
                 color='white', fontsize=16, fontweight='bold', y=0.995)

    # Save
    output_path = os.path.join(session_dir, 'lap_analysis.png')
    plt.savefig(output_path, dpi=150, facecolor=fig.get_facecolor(),
                edgecolor='none', bbox_inches='tight')
    plt.close(fig)

    # --- Generate delta-to-best plot ---
    _generate_delta_plot(session_dir, valid_laps, lap_nums, best_lap,
                         lap_colors, lap_times)

    # --- Mini-sector analysis ---
    try:
        from telemetry.mini_sectors import (
            compute_lap_sectors, compute_theoretical_best, render_sector_plot,
        )
        # Pass the full LapTime so sector 0 and sector 20 anchor correctly
        # at the S/F line (t=0 and t=LapTime respectively). lap_times keys
        # are now 0-based (see get_lap_times_from_summary docstring).
        laps_sectors = {ln: compute_lap_sectors(valid_laps[ln],
                                                 lap_time=lap_times.get(ln))
                        for ln in lap_nums}
        tb_total, donors = compute_theoretical_best(laps_sectors)
        render_sector_plot(session_dir, laps_sectors, lap_times, best_lap,
                            tb_total, donors)
        if tb_total is not None:
            mins = int(tb_total // 60)
            secs = tb_total - mins * 60
            print(f"[lap_analysis] Theoretische Bestzeit: {mins}:{secs:05.2f}  "
                  f"(Sektor-Donors: {donors})")
    except Exception as e:
        print(f"[lap_analysis] mini_sectors failed: {e}", file=sys.stderr)

    # --- Brake / throttle / steering variance analysis ---
    try:
        from telemetry.variance_analysis import (
            detect_brake_points, detect_throttle_releases, detect_steering_events,
            cluster_events_across_laps, render_variance_plot,
        )
        brake_events = {ln: detect_brake_points(valid_laps[ln]) for ln in lap_nums}
        throttle_events = {ln: detect_throttle_releases(valid_laps[ln]) for ln in lap_nums}
        steering_events = {ln: detect_steering_events(valid_laps[ln]) for ln in lap_nums}
        brake_clusters = cluster_events_across_laps(brake_events)
        throttle_clusters = cluster_events_across_laps(throttle_events)
        steering_clusters = cluster_events_across_laps(steering_events)
        render_variance_plot(session_dir, brake_clusters, throttle_clusters,
                              steering_clusters=steering_clusters)
    except Exception as e:
        print(f"[lap_analysis] variance_analysis failed: {e}", file=sys.stderr)

    return output_path


def _generate_delta_plot(session_dir, valid_laps, lap_nums, best_lap,
                         lap_colors, lap_times):
    """Generate a separate plot showing time delta to best lap at each track position.

    For each channel, calculates: value_this_lap - value_best_lap
    This shows exactly WHERE and HOW MUCH each lap differs from the best.
    """
    if best_lap is None or best_lap not in valid_laps:
        return

    # Resample best lap for all channels
    best_data = {}
    best_pcts = None
    for col, name, unit, inv in CHANNELS:
        pcts, vals = _resample_lap(valid_laps[best_lap], col)
        if pcts is not None:
            best_data[col] = vals
            if best_pcts is None:
                best_pcts = pcts

    if not best_data or best_pcts is None:
        return

    # Focus on the most insightful delta channels
    delta_channels = [
        ('Speed_kmh',    'Speed Delta',   'km/h'),
        ('Throttle',     'Gas Delta',     '%'),
        ('Brake',        'Bremse Delta',  '%'),
    ]

    fig, axes = plt.subplots(len(delta_channels), 1, figsize=(18, 3 * len(delta_channels)),
                              sharex=True)
    fig.patch.set_facecolor('#1a1a2e')

    if len(delta_channels) == 1:
        axes = [axes]

    for ch_idx, (col, name, unit) in enumerate(delta_channels):
        ax = axes[ch_idx]
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white', labelsize=8)
        ax.grid(True, alpha=0.15, color='white')
        for spine in ax.spines.values():
            spine.set_color('#333333')

        if col not in best_data:
            continue

        best_vals = best_data[col]

        for lap_num in lap_nums:
            if lap_num == best_lap:
                continue  # Best lap delta to itself = 0, skip

            pcts, vals = _resample_lap(valid_laps[lap_num], col)
            if pcts is None:
                continue

            # Resample to match best lap's pct grid
            vals_aligned = np.interp(best_pcts, pcts, vals)
            delta = vals_aligned - best_vals

            color = lap_colors[lap_num]
            lt = lap_times.get(lap_num)
            label = f'R{lap_num}'
            if lt:
                mins = int(lt // 60)
                secs = lt % 60
                label += f' ({mins}:{secs:05.2f})'

            ax.plot(best_pcts, delta, color=color, linewidth=1.0,
                   alpha=0.7, label=label)

        # Zero line (= best lap)
        ax.axhline(y=0, color='#00FF66', linewidth=1.5, linestyle='--',
                   alpha=0.8, label=f'Best (R{best_lap})')

        # Color zones: green above 0 (faster than best = shouldn't happen much for speed)
        # For speed: positive delta = faster than best in that spot
        ax.fill_between(best_pcts, 0, ax.get_ylim()[1] if ax.get_ylim()[1] > 0 else 10,
                        alpha=0.03, color='green')
        ax.fill_between(best_pcts, ax.get_ylim()[0] if ax.get_ylim()[0] < 0 else -10, 0,
                        alpha=0.03, color='red')

        ax.set_ylabel(f'{name} [{unit}]', color='white', fontsize=10,
                      fontweight='bold')

        if ch_idx == 0:
            ax.legend(loc='upper right', fontsize=7,
                     ncol=min(len(lap_nums), 5),
                     facecolor='#1a1a2e', edgecolor='#333333',
                     labelcolor='white')

    axes[-1].set_xlabel('Streckenposition [%]', color='white', fontsize=11)

    fig.suptitle(f'Delta zur besten Runde (R{best_lap})',
                 color='white', fontsize=16, fontweight='bold', y=0.995)

    output_path = os.path.join(session_dir, 'lap_delta_analysis.png')
    plt.savefig(output_path, dpi=150, facecolor=fig.get_facecolor(),
                edgecolor='none', bbox_inches='tight')
    plt.close(fig)
