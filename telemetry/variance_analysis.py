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


BRAKE_THRESHOLD_PCT = 3.0        # Brake > 3% = "applied" (catches trail/rotation brushes)
THROTTLE_RELEASE_PCT = 90.0      # Throttle < 90% = "released"
STEERING_THRESHOLD_RAD = 0.2     # |SteeringWheelAngle| > ~11° = turning in
CLUSTER_EPSILON = 0.015          # 1.5% of lap distance ≈ same corner


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


def detect_steering_events(lap_ticks, threshold=STEERING_THRESHOLD_RAD):
    """Rising-edge pcts where |SteeringWheelAngle| crosses above threshold.

    Direction-agnostic (abs) so left and right turn-ins are treated the same.
    Default 0.2 rad (~11°) is big enough to ignore straight-line corrections
    and catches the turn-in point of every real corner, including gentle
    sweepers.
    """
    events = []
    prev = 0.0
    for pct, v in lap_ticks:
        s = v.get('SteeringWheelAngle')
        if s is None:
            continue
        s_abs = abs(s)
        if prev <= threshold < s_abs:
            events.append(pct)
        prev = s_abs
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


def build_variance_figure(brake_clusters, throttle_clusters,
                            steering_clusters=None, figure=None):
    """Render brake / throttle / steering variance charts onto a Figure
    (OO API, any backend).

    If `figure` is provided, draws into it (Qt embedding); otherwise makes
    a new one (PNG export). Returns None if all cluster lists are empty.

    `steering_clusters` is optional for backward compat — older callers
    that pre-date the corner-detection feature still work.
    """
    from matplotlib.figure import Figure

    steering_clusters = steering_clusters or []
    if not brake_clusters and not throttle_clusters and not steering_clusters:
        return None

    panels = [
        (brake_clusters,    '#ff5555', 'Bremspunkt-Konsistenz (harte Bremszonen)'),
        (throttle_clusters, '#55ff55', 'Throttle-Release Konsistenz (Lift-Punkte)'),
        (steering_clusters, '#7fb8e8', 'Kurven-Einlenken Konsistenz (Turn-in Punkte)'),
    ]

    if figure is None:
        figure = Figure(figsize=(18, 12), facecolor='#1a1a2e')
    else:
        figure.clear()
        figure.patch.set_facecolor('#1a1a2e')

    n = len(panels)
    axes = [figure.add_subplot(n, 1, i + 1) for i in range(n)]

    for ax, (clusters, color, title) in zip(axes, panels):
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


def render_variance_plot(session_dir, brake_clusters, throttle_clusters,
                          steering_clusters=None):
    """Write brake_throttle_variance.png to disk. Thin wrapper over
    build_variance_figure."""
    fig = build_variance_figure(brake_clusters, throttle_clusters,
                                  steering_clusters=steering_clusters)
    if fig is None:
        return None
    out_path = os.path.join(session_dir, 'brake_throttle_variance.png')
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(),
                 edgecolor='none', bbox_inches='tight')
    return out_path
