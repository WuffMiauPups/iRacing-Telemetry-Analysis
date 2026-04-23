"""Brake-point and throttle-release consistency analysis.

For each lap, find the LapDistPct where the driver starts braking at each corner
(brake rising edge above 10%) and where they release throttle (throttle falling
edge below 90%). Cluster across laps by pct proximity — each cluster ≈ one corner.
Report per-corner min/max/mean/std of event positions.

Note: per _load_and_group_laps, Throttle and Brake are already scaled to 0–100.
"""

import os
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


BRAKE_THRESHOLD_PCT = 10.0       # Brake > 10% = "applied"
THROTTLE_RELEASE_PCT = 90.0      # Throttle < 90% = "released"
CLUSTER_EPSILON = 0.03           # 3% of lap distance ≈ same corner


def detect_brake_points(lap_ticks, threshold=BRAKE_THRESHOLD_PCT):
    """Rising-edge pcts where Brake crosses above threshold."""
    events = []
    prev = 0.0
    for pct, v in lap_ticks:
        b = v.get('Brake')
        if b is None:
            continue
        if prev <= threshold < b:
            events.append(pct)
        prev = b
    return events


def detect_throttle_releases(lap_ticks, threshold=THROTTLE_RELEASE_PCT):
    """Falling-edge pcts where Throttle crosses below threshold."""
    events = []
    prev = None
    for pct, v in lap_ticks:
        t = v.get('Throttle')
        if t is None:
            continue
        if prev is not None and prev >= threshold > t:
            events.append(pct)
        prev = t
    return events


def cluster_events_across_laps(events_per_lap, epsilon=CLUSTER_EPSILON):
    """Cluster events by pct proximity.

    events_per_lap: dict lap_num -> list[float] of pcts
    Returns: list of dicts [{mean, std, min, max, n_laps, per_lap: {lap: pct}}]
    sorted by mean pct ascending.
    """
    flat = []
    for lap_num, pcts in events_per_lap.items():
        for p in pcts:
            flat.append((p, lap_num))
    if not flat:
        return []

    flat.sort(key=lambda x: x[0])

    clusters = []
    current = [flat[0]]
    for p, lap_num in flat[1:]:
        if p - current[-1][0] <= epsilon:
            current.append((p, lap_num))
        else:
            clusters.append(current)
            current = [(p, lap_num)]
    clusters.append(current)

    # Only report clusters with 2+ laps (otherwise it's a one-off noise)
    result = []
    for c in clusters:
        pcts = [p for p, _ in c]
        per_lap = {}
        for p, ln in c:
            # If a lap has multiple events in the same cluster, keep the earliest
            if ln not in per_lap or p < per_lap[ln]:
                per_lap[ln] = p
        if len(per_lap) < 2:
            continue
        arr = np.array(list(per_lap.values()))
        result.append({
            'mean': float(arr.mean()),
            'std': float(arr.std()),
            'min': float(arr.min()),
            'max': float(arr.max()),
            'n_laps': len(per_lap),
            'per_lap': per_lap,
        })
    return result


def build_variance_figure(brake_clusters, throttle_clusters, figure=None):
    """Render brake/throttle variance chart onto a Figure (OO API, any backend).

    If `figure` is provided, draws into it (Qt embedding); otherwise makes a
    new one (PNG export). Returns None if both cluster lists are empty.
    """
    from matplotlib.figure import Figure

    if not brake_clusters and not throttle_clusters:
        return None

    if figure is None:
        figure = Figure(figsize=(18, 8), facecolor='#1a1a2e')
    else:
        figure.clear()
        figure.patch.set_facecolor('#1a1a2e')

    ax_b = figure.add_subplot(2, 1, 1)
    ax_t = figure.add_subplot(2, 1, 2)

    for ax, clusters, color, title in (
        (ax_b, brake_clusters, '#ff5555', 'Brake-Point Konsistenz (Runde zu Runde, nach Kurve)'),
        (ax_t, throttle_clusters, '#55ff55', 'Throttle-Release Konsistenz (nach Kurve)'),
    ):
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white', labelsize=9)
        ax.grid(True, alpha=0.15, color='white', axis='y')
        for spine in ax.spines.values():
            spine.set_color('#333333')

        if not clusters:
            ax.text(0.5, 0.5, 'Keine konsistenten Events erkannt',
                    color='#888888', ha='center', va='center',
                    transform=ax.transAxes, fontsize=11)
            ax.set_title(title, color='white', fontsize=12, fontweight='bold')
            continue

        xs = np.arange(len(clusters))
        means = [c['mean'] * 100 for c in clusters]
        stds = [c['std'] * 100 for c in clusters]
        mins = [c['min'] * 100 for c in clusters]
        maxs = [c['max'] * 100 for c in clusters]

        ax.bar(xs, means, yerr=stds, color=color, alpha=0.7,
               edgecolor='white', linewidth=0.5, capsize=4,
               error_kw={'ecolor': 'white', 'alpha': 0.6})

        for x, mn, mx in zip(xs, mins, maxs):
            ax.plot([x, x], [mn, mx], color='white', alpha=0.3, linewidth=1.0)

        for x, c, mean in zip(xs, clusters, means):
            ax.text(x, mean + 1, f"n={c['n_laps']}\nσ={c['std']*100:.2f}%",
                    color='white', ha='center', fontsize=7, va='bottom')

        ax.set_xticks(xs)
        ax.set_xticklabels([f"K{i+1}\n@{c['mean']*100:.1f}%"
                            for i, c in enumerate(clusters)],
                           color='white', fontsize=8)
        ax.set_ylabel('Streckenposition [%]', color='white', fontsize=10)
        ax.set_title(title, color='white', fontsize=12, fontweight='bold')

    figure.tight_layout()
    return figure


def render_variance_plot(session_dir, brake_clusters, throttle_clusters):
    """Write brake_throttle_variance.png to disk. Thin wrapper over
    build_variance_figure."""
    fig = build_variance_figure(brake_clusters, throttle_clusters)
    if fig is None:
        return None
    out_path = os.path.join(session_dir, 'brake_throttle_variance.png')
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(),
                 edgecolor='none', bbox_inches='tight')
    return out_path
